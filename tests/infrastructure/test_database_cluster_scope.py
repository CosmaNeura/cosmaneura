from __future__ import annotations

import io
from contextlib import nullcontext

import pytest

from infra import database_cluster_scope as scope


def _exposure(
    *,
    database: str = "postgres",
    classification: str = "actionable",
    public_connect: bool = True,
    public_temporary: bool = True,
    preserve_connect: tuple[str, ...] = ("admin_login",),
    preserve_temporary: tuple[str, ...] = ("admin_login",),
    application_connect: bool = False,
    owner_temporary: bool = False,
) -> scope.DatabaseExposure:
    return scope.DatabaseExposure(
        database=database,
        owner="admin_login",
        classification=classification,
        allows_connections=True,
        is_template=False,
        public_connect=public_connect,
        public_temporary=public_temporary,
        operator_can_manage=True,
        preserve_connect_roles=preserve_connect,
        preserve_temporary_roles=preserve_temporary,
        application_connect=application_connect,
        owner_temporary=owner_temporary,
    )


def _plan(*exposures: scope.DatabaseExposure) -> scope.ScopePlan:
    return scope.ScopePlan(
        current_database="production",
        current_user="admin_login",
        target_role_exists=False,
        target_other_database_sessions=0,
        exposures=exposures,
    )


def test_database_classification_never_automates_managed_or_template_databases() -> (
    None
):
    assert scope.classify_database("production", is_template=False) == "target"
    assert scope.classify_database("postgres", is_template=False) == "actionable"
    assert scope.classify_database("azure_sys", is_template=False) == "audit-only"
    assert (
        scope.classify_database("azure_maintenance", is_template=False) == "audit-only"
    )
    assert scope.classify_database("template1", is_template=True) == "audit-only"


def test_plan_digest_covers_preservation_roles_and_hard_blockers() -> None:
    plan = _plan(
        _exposure(),
        _exposure(database="azure_sys", classification="audit-only"),
    )
    changed = _plan(_exposure(preserve_connect=("different_login",)))

    assert plan.digest() != changed.digest()
    assert [item.database for item in plan.actionable()] == ["postgres"]
    assert [item.database for item in plan.hard_blockers()] == ["azure_sys"]


def test_effective_application_role_access_is_a_blocker_without_public_acl() -> None:
    postgres = _exposure(
        public_connect=False,
        public_temporary=False,
        application_connect=True,
        preserve_connect=(),
        preserve_temporary=(),
    )
    managed = _exposure(
        database="azure_sys",
        classification="audit-only",
        public_connect=False,
        public_temporary=False,
        owner_temporary=True,
        preserve_connect=(),
        preserve_temporary=(),
    )
    plan = _plan(postgres, managed)

    assert [item.database for item in plan.actionable()] == ["postgres"]
    assert [item.database for item in plan.hard_blockers()] == ["azure_sys"]


def test_catalog_inventory_keeps_empty_acl_database_with_effective_access() -> None:
    class Result:
        def __init__(self, *, one: tuple[object, ...] | None = None, many=()):
            self.one = one
            self.many = list(many)

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.many

    class Connection:
        def __init__(self) -> None:
            self.results = iter(
                (
                    Result(one=("production", "admin_login")),
                    Result(one=(True, True)),
                    Result(one=(0,)),
                    Result(
                        many=[
                            (
                                "unknown_empty_acl",
                                "admin_login",
                                True,
                                False,
                                False,
                                False,
                                True,
                                True,
                                False,
                                False,
                                False,
                            )
                        ]
                    ),
                )
            )
            self.statements: list[object] = []

        def execute(self, statement: object, *args: object) -> Result:
            self.statements.append(statement)
            return next(self.results)

    connection = Connection()
    plan = scope.load_plan(connection)  # type: ignore[arg-type]

    catalog_sql = str(connection.statements[3])
    assert "LEFT JOIN LATERAL aclexplode" in catalog_sql
    assert "COALESCE(bool_or" in catalog_sql
    assert [item.database for item in plan.hard_blockers()] == ["unknown_empty_acl"]


def test_apply_requires_exact_preservation_sets_before_sql() -> None:
    plan = _plan(_exposure())

    with pytest.raises(scope.ClusterScopeError, match="CONNECT preservation"):
        scope.apply_plan(
            object(),  # type: ignore[arg-type]
            plan=plan,
            confirmed_digest=plan.digest(),
            confirmed_databases={"postgres"},
            preserve_connect_roles=set(),
            preserve_temporary_roles={"admin_login"},
        )


def test_apply_grants_preservation_before_public_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_exposure())

    class FakeConnection:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def transaction(self):
            return nullcontext()

        def execute(self, statement: object, *args: object):
            self.calls.append(statement)
            return self

    connection = FakeConnection()
    monkeypatch.setattr(scope, "verify_remediation", lambda connection: plan)

    scope.apply_plan(
        connection,  # type: ignore[arg-type]
        plan=plan,
        confirmed_digest=plan.digest(),
        confirmed_databases={"postgres"},
        preserve_connect_roles={"admin_login"},
        preserve_temporary_roles={"admin_login"},
    )

    rendered = [repr(item) for item in connection.calls]
    grant_positions = [i for i, value in enumerate(rendered) if "GRANT" in value]
    revoke_positions = [i for i, value in enumerate(rendered) if "REVOKE" in value]
    assert grant_positions and revoke_positions
    assert max(grant_positions) < min(revoke_positions)


def test_apply_requires_exact_actionable_database_confirmation_before_sql() -> None:
    plan = _plan(_exposure())

    with pytest.raises(scope.ClusterScopeError, match="database confirmations"):
        scope.apply_plan(
            object(),  # type: ignore[arg-type]
            plan=plan,
            confirmed_digest=plan.digest(),
            confirmed_databases={"production"},
            preserve_connect_roles={"admin_login"},
            preserve_temporary_roles={"admin_login"},
        )


def test_admin_url_accepts_one_non_cli_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUTOVER_DATABASE_URL", raising=False)
    value = (
        "postgresql://admin:opaque@"
        "cosmaneura-prod-postgres.postgres.database.azure.com/"
        "production?sslmode=require"
    )
    monkeypatch.setattr(scope.sys, "stdin", io.StringIO(f"{value}\n"))
    assert (
        scope.read_admin_url(env_name="CUTOVER_DATABASE_URL", from_stdin=True) == value
    )


def test_apply_confirmation_fails_before_database_access(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        scope,
        "read_admin_url",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("secret read")),
    )
    assert scope.main(["--apply"]) == 2
    assert "confirmation_mismatch" in capsys.readouterr().out
