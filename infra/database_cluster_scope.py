#!/usr/bin/env python3
"""Audit and narrowly remediate cross-database privileges.

Default mode is a read-only cluster-catalog audit.  The only automated
remediation target is the ordinary ``postgres`` maintenance database.  The
audit expands ``PUBLIC`` ACL defaults and, when present, checks effective access
for both application roles.  Azure-managed and template databases are reported
as hard blockers and are never mutated by this tool.  Apply requires the exact
dry-run digest, explicit impact acknowledgements, and exact role-preservation
lists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from psycopg import sql

if __package__:
    from .database_role import (
        APPLICATION_LOGIN_ROLE,
        APPLICATION_OWNER_ROLE,
        PRODUCTION_DATABASE_HOST,
        PRODUCTION_DATABASE_NAME,
        RolePreparationError,
        validate_admin_database_url,
    )
else:  # Support the documented ``python infra/database_cluster_scope.py`` form.
    from database_role import (  # type: ignore[import-not-found]
        APPLICATION_LOGIN_ROLE,
        APPLICATION_OWNER_ROLE,
        PRODUCTION_DATABASE_HOST,
        PRODUCTION_DATABASE_NAME,
        RolePreparationError,
        validate_admin_database_url,
    )


class ClusterScopeError(RuntimeError):
    """A sanitized cluster-scope operator failure."""


@dataclass(frozen=True)
class DatabaseExposure:
    database: str
    owner: str
    classification: str
    allows_connections: bool
    is_template: bool
    public_connect: bool
    public_temporary: bool
    operator_can_manage: bool
    preserve_connect_roles: tuple[str, ...]
    preserve_temporary_roles: tuple[str, ...]
    application_connect: bool = False
    application_temporary: bool = False
    owner_connect: bool = False
    owner_temporary: bool = False

    def has_connect_exposure(self) -> bool:
        return self.public_connect or self.application_connect or self.owner_connect

    def has_temporary_exposure(self) -> bool:
        return (
            self.public_temporary or self.application_temporary or self.owner_temporary
        )


@dataclass(frozen=True)
class ScopePlan:
    current_database: str
    current_user: str
    target_role_exists: bool
    target_other_database_sessions: int
    exposures: tuple[DatabaseExposure, ...]
    owner_role_exists: bool = False

    def canonical(self) -> dict[str, Any]:
        return {
            "current_database": self.current_database,
            "current_user": self.current_user,
            "target_role_exists": self.target_role_exists,
            "owner_role_exists": self.owner_role_exists,
            "target_other_database_sessions": self.target_other_database_sessions,
            "exposures": [asdict(item) for item in self.exposures],
        }

    def digest(self) -> str:
        encoded = json.dumps(
            self.canonical(), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def actionable(self) -> tuple[DatabaseExposure, ...]:
        return tuple(
            item
            for item in self.exposures
            if item.classification == "actionable"
            and (item.has_connect_exposure() or item.has_temporary_exposure())
        )

    def hard_blockers(self) -> tuple[DatabaseExposure, ...]:
        return tuple(
            item
            for item in self.exposures
            if item.classification == "audit-only"
            and item.allows_connections
            and (item.has_connect_exposure() or item.has_temporary_exposure())
        )

    def summary(self) -> dict[str, Any]:
        return {
            "actionable": [item.database for item in self.actionable()],
            "databases": [asdict(item) for item in self.exposures],
            "hard_blockers": [item.database for item in self.hard_blockers()],
            "plan_digest": self.digest(),
            "owner_role_exists": self.owner_role_exists,
            "target_role_exists": self.target_role_exists,
            "target_other_database_sessions": self.target_other_database_sessions,
        }


def classify_database(name: str, *, is_template: bool) -> str:
    if name == PRODUCTION_DATABASE_NAME:
        return "target"
    if name == "postgres" and not is_template:
        return "actionable"
    return "audit-only"


def _preserve_roles(
    connection: psycopg.Connection[Any],
    *,
    database: str,
    privilege: str,
) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT rolname
          FROM pg_roles
         WHERE rolcanlogin
           AND rolname <> %s
           AND has_database_privilege(rolname, %s, %s)
         ORDER BY rolname
        """,
        (APPLICATION_LOGIN_ROLE, database, privilege),
    ).fetchall()
    return tuple(row[0] for row in rows)


def load_plan(connection: psycopg.Connection[Any]) -> ScopePlan:
    identity = connection.execute("SELECT current_database(), current_user").fetchone()
    if identity is None:
        raise ClusterScopeError("database identity is unavailable")
    if identity[0] != PRODUCTION_DATABASE_NAME:
        raise ClusterScopeError("connected to an unexpected database")
    target_role_row = connection.execute(
        """
        SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = %s),
               EXISTS(SELECT 1 FROM pg_roles WHERE rolname = %s)
        """,
        (APPLICATION_LOGIN_ROLE, APPLICATION_OWNER_ROLE),
    ).fetchone()
    if target_role_row is None:
        raise ClusterScopeError("target role inventory is unavailable")
    target_role_exists = bool(target_role_row[0])
    owner_role_exists = bool(target_role_row[1])
    target_other_database_sessions = 0
    if target_role_exists:
        session_row = connection.execute(
            """
            SELECT count(*)
              FROM pg_stat_activity
             WHERE usename = %s
               AND datname IS DISTINCT FROM %s
            """,
            (APPLICATION_LOGIN_ROLE, PRODUCTION_DATABASE_NAME),
        ).fetchone()
        if session_row is None:
            raise ClusterScopeError("target role session inventory is unavailable")
        target_other_database_sessions = session_row[0]
    rows = connection.execute(
        """
        SELECT database.datname,
               owner.rolname,
               database.datallowconn,
               database.datistemplate,
               COALESCE(bool_or(
                   acl.grantee = 0 AND acl.privilege_type = 'CONNECT'
               ), false) AS public_connect,
               COALESCE(bool_or(
                   acl.grantee = 0 AND acl.privilege_type = 'TEMPORARY'
               ), false) AS public_temporary,
               pg_has_role(current_user, owner.rolname, 'USAGE'),
               CASE WHEN to_regrole(%s) IS NOT NULL
                    THEN has_database_privilege(%s, database.datname, 'CONNECT')
                    ELSE false END AS application_connect,
               CASE WHEN to_regrole(%s) IS NOT NULL
                    THEN has_database_privilege(%s, database.datname, 'TEMPORARY')
                    ELSE false END AS application_temporary,
               CASE WHEN to_regrole(%s) IS NOT NULL
                    THEN has_database_privilege(%s, database.datname, 'CONNECT')
                    ELSE false END AS owner_connect,
               CASE WHEN to_regrole(%s) IS NOT NULL
                    THEN has_database_privilege(%s, database.datname, 'TEMPORARY')
                    ELSE false END AS owner_temporary
          FROM pg_database AS database
          JOIN pg_roles AS owner ON owner.oid = database.datdba
          LEFT JOIN LATERAL aclexplode(
              COALESCE(database.datacl, acldefault('d', database.datdba))
          ) AS acl ON true
         GROUP BY database.datname, owner.rolname, database.datallowconn,
                  database.datistemplate, database.datdba
         ORDER BY database.datname
        """,
        (
            APPLICATION_LOGIN_ROLE,
            APPLICATION_LOGIN_ROLE,
            APPLICATION_LOGIN_ROLE,
            APPLICATION_LOGIN_ROLE,
            APPLICATION_OWNER_ROLE,
            APPLICATION_OWNER_ROLE,
            APPLICATION_OWNER_ROLE,
            APPLICATION_OWNER_ROLE,
        ),
    ).fetchall()
    exposures: list[DatabaseExposure] = []
    for row in rows:
        classification = classify_database(row[0], is_template=row[3])
        preserve_connect: tuple[str, ...] = ()
        preserve_temporary: tuple[str, ...] = ()
        if classification == "actionable":
            if row[4]:
                preserve_connect = _preserve_roles(
                    connection, database=row[0], privilege="CONNECT"
                )
            if row[5]:
                preserve_temporary = _preserve_roles(
                    connection, database=row[0], privilege="TEMPORARY"
                )
        exposures.append(
            DatabaseExposure(
                database=row[0],
                owner=row[1],
                classification=classification,
                allows_connections=row[2],
                is_template=row[3],
                public_connect=row[4],
                public_temporary=row[5],
                operator_can_manage=row[6],
                preserve_connect_roles=preserve_connect,
                preserve_temporary_roles=preserve_temporary,
                application_connect=row[7],
                application_temporary=row[8],
                owner_connect=row[9],
                owner_temporary=row[10],
            )
        )
    return ScopePlan(
        current_database=identity[0],
        current_user=identity[1],
        target_role_exists=target_role_exists,
        target_other_database_sessions=target_other_database_sessions,
        exposures=tuple(exposures),
        owner_role_exists=owner_role_exists,
    )


def apply_plan(
    connection: psycopg.Connection[Any],
    *,
    plan: ScopePlan,
    confirmed_digest: str,
    confirmed_databases: set[str],
    preserve_connect_roles: set[str],
    preserve_temporary_roles: set[str],
) -> None:
    if not plan.actionable():
        raise ClusterScopeError("there are no actionable other-database privileges")
    if confirmed_digest != plan.digest():
        raise ClusterScopeError("cluster-scope plan digest does not match")
    actionable_databases = {item.database for item in plan.actionable()}
    if confirmed_databases != actionable_databases:
        raise ClusterScopeError("actionable database confirmations do not match")
    if any(not item.operator_can_manage for item in plan.actionable()):
        raise ClusterScopeError("operator cannot manage an actionable database")
    if plan.target_other_database_sessions:
        raise ClusterScopeError("application login has active other-database sessions")
    expected_connect = {
        role for item in plan.actionable() for role in item.preserve_connect_roles
    }
    expected_temporary = {
        role for item in plan.actionable() for role in item.preserve_temporary_roles
    }
    if preserve_connect_roles != expected_connect:
        raise ClusterScopeError("CONNECT preservation roles do not match the plan")
    if preserve_temporary_roles != expected_temporary:
        raise ClusterScopeError("TEMPORARY preservation roles do not match the plan")

    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '60s'")
        for exposure in plan.actionable():
            for role in exposure.preserve_connect_roles:
                connection.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(exposure.database), sql.Identifier(role)
                    )
                )
            for role in exposure.preserve_temporary_roles:
                connection.execute(
                    sql.SQL("GRANT TEMPORARY ON DATABASE {} TO {}").format(
                        sql.Identifier(exposure.database), sql.Identifier(role)
                    )
                )
            privileges: list[sql.SQL] = []
            if exposure.has_connect_exposure():
                privileges.append(sql.SQL("CONNECT"))
            if exposure.has_temporary_exposure():
                privileges.append(sql.SQL("TEMPORARY"))
            if exposure.public_connect or exposure.public_temporary:
                public_privileges: list[sql.SQL] = []
                if exposure.public_connect:
                    public_privileges.append(sql.SQL("CONNECT"))
                if exposure.public_temporary:
                    public_privileges.append(sql.SQL("TEMPORARY"))
                connection.execute(
                    sql.SQL("REVOKE {} ON DATABASE {} FROM PUBLIC").format(
                        sql.SQL(", ").join(public_privileges),
                        sql.Identifier(exposure.database),
                    )
                )
            for role, exists in (
                (APPLICATION_LOGIN_ROLE, plan.target_role_exists),
                (APPLICATION_OWNER_ROLE, plan.owner_role_exists),
            ):
                if exists:
                    connection.execute(
                        sql.SQL("REVOKE {} ON DATABASE {} FROM {}").format(
                            sql.SQL(", ").join(privileges),
                            sql.Identifier(exposure.database),
                            sql.Identifier(role),
                        )
                    )
        # Verify before leaving the transaction so a failed postcondition rolls
        # back every grant/revoke instead of leaving a partial remediation.
        verify_remediation(connection)


def verify_remediation(connection: psycopg.Connection[Any]) -> ScopePlan:
    verified = load_plan(connection)
    if verified.actionable():
        raise ClusterScopeError("actionable other-database privileges remain")
    for role, exists in (
        (APPLICATION_LOGIN_ROLE, verified.target_role_exists),
        (APPLICATION_OWNER_ROLE, verified.owner_role_exists),
    ):
        if not exists:
            continue
        exposed_row = connection.execute(
            """
            SELECT count(*)
              FROM pg_database
             WHERE datname = 'postgres'
               AND datallowconn
               AND (
                   has_database_privilege(%s, datname, 'CONNECT')
                   OR has_database_privilege(%s, datname, 'TEMPORARY')
               )
            """,
            (
                role,
                role,
            ),
        ).fetchone()
        if exposed_row is None:
            raise ClusterScopeError("effective privilege verification is unavailable")
        exposed = exposed_row[0]
        if exposed:
            raise ClusterScopeError(
                "application role still has effective other-database privileges"
            )
    return verified


def read_admin_url(*, env_name: str, from_stdin: bool) -> str:
    env_value = os.environ.get(env_name)
    if env_value is not None and from_stdin:
        raise ClusterScopeError("provide the admin URL through one source only")
    if env_value is not None:
        value = env_value
    elif from_stdin:
        value = sys.stdin.readline().removesuffix("\n")
    else:
        raise ClusterScopeError("administrative database URL is missing")
    if not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise ClusterScopeError("administrative database URL is invalid")
    try:
        validate_admin_database_url(value, expected_database=PRODUCTION_DATABASE_NAME)
    except RolePreparationError as error:
        raise ClusterScopeError("administrative database URL is invalid") from error
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--admin-url-env", default="DATABASE_URL")
    parser.add_argument("--admin-url-stdin", action="store_true")
    parser.add_argument("--confirm-plan-digest")
    parser.add_argument("--confirm-host")
    parser.add_argument("--confirm-database")
    parser.add_argument("--confirm-actionable-database", action="append", default=[])
    parser.add_argument("--acknowledge-public-impact", action="store_true")
    parser.add_argument("--acknowledge-audit-only-blockers", action="store_true")
    parser.add_argument("--preserve-connect-role", action="append", default=[])
    parser.add_argument("--preserve-temporary-role", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and (
        args.confirm_host != PRODUCTION_DATABASE_HOST
        or args.confirm_database != PRODUCTION_DATABASE_NAME
        or not args.confirm_plan_digest
        or not args.acknowledge_public_impact
    ):
        print(json.dumps({"code": "confirmation_mismatch", "result": "failed"}))
        return 2
    try:
        admin_url = read_admin_url(
            env_name=args.admin_url_env, from_stdin=args.admin_url_stdin
        )
        with psycopg.connect(admin_url) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            plan = load_plan(connection)
            connection.rollback()
            if not args.apply:
                print(
                    json.dumps(
                        {"mode": "dry-run", "result": "planned", **plan.summary()},
                        sort_keys=True,
                    )
                )
                return 0
            if plan.hard_blockers() and not args.acknowledge_audit_only_blockers:
                raise ClusterScopeError(
                    "audit-only database exposures require explicit acknowledgement"
                )
            apply_plan(
                connection,
                plan=plan,
                confirmed_digest=args.confirm_plan_digest,
                confirmed_databases=set(args.confirm_actionable_database),
                preserve_connect_roles=set(args.preserve_connect_role),
                preserve_temporary_roles=set(args.preserve_temporary_role),
            )
            verified = verify_remediation(connection)
            print(
                json.dumps(
                    {
                        "hard_blockers": [
                            item.database for item in verified.hard_blockers()
                        ],
                        "mode": "apply",
                        "result": "succeeded",
                    },
                    sort_keys=True,
                )
            )
            return 0
    except (ClusterScopeError, psycopg.Error):
        print(json.dumps({"code": "cluster_scope_failed", "result": "failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
