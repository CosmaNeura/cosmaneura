from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import pytest

from infra import migration_91d2c4e5f6a7_preflight as preflight


class Result:
    def __init__(
        self,
        *,
        one: tuple[Any, ...] | None = None,
        many: Iterable[tuple[Any, ...]] = (),
    ) -> None:
        self.one = one
        self.many = list(many)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.many


class FakeConnection:
    def __init__(self) -> None:
        self.results = iter(
            (
                Result(one=("production", "admin", "16.14")),
                Result(many=[(preflight.EXPECTED_CURRENT_REVISION,)]),
                Result(one=(False,)),
                Result(one=(False,)),
                Result(one=(0,)),
                Result(one=(0,)),
                Result(one=(0,)),
                Result(one=(1250, 1024, 4096)),
                Result(many=[("AccessShareLock", True, 1)]),
                Result(one=(0,)),
            )
        )
        self.statements: list[object] = []

    def execute(self, statement: object, *args: object) -> Result:
        self.statements.append(statement)
        return next(self.results)


def test_preflight_reports_counts_only_and_ready_state() -> None:
    connection = FakeConnection()

    result = preflight.inspect(connection)  # type: ignore[arg-type]

    assert result["readiness"] == {
        "expected_current_revision": True,
        "new_objects_absent": True,
        "patient_scope_unique": True,
        "ready": True,
    }
    assert result["new_table_row_counts"] == {
        "ehr_sandbox_patient_designation": None,
        "ehr_sandbox_record_mapping": None,
    }
    assert result["patient"] == {
        "duplicate_id_doctor_group_count": 0,
        "estimated_rows": 1250,
        "null_doctor_row_count": 0,
        "relation_bytes": 1024,
        "total_relation_bytes": 4096,
    }
    assert result["runtime"]["long_transaction_count"] == 0


def test_target_revision_or_object_conflict_is_not_ready() -> None:
    connection = FakeConnection()
    replacement = list(connection.results)
    replacement[1] = Result(many=[(preflight.TARGET_REVISION,)])
    connection.results = iter(replacement)

    result = preflight.inspect(connection)  # type: ignore[arg-type]

    assert result["alembic"]["target_already_applied"] is True
    assert result["readiness"]["ready"] is False


def test_main_exits_nonzero_when_readiness_is_false(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, *args: object) -> None:
            return None

        def rollback(self) -> None:
            return None

    monkeypatch.setenv("DATABASE_URL", "postgresql://opaque")
    monkeypatch.setattr(
        preflight, "validate_admin_database_url", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        preflight.psycopg, "connect", lambda *args, **kwargs: Connection()
    )
    monkeypatch.setattr(
        preflight,
        "inspect",
        lambda connection: {"readiness": {"ready": False}},
    )

    assert preflight.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "migration_not_ready"
    assert output["result"] == "blocked"


def test_main_rejects_require_only_tls_before_database_access(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://admin:opaque@"
        "cosmaneura-prod-postgres.postgres.database.azure.com/"
        "production?sslmode=require",
    )
    monkeypatch.setattr(
        preflight.psycopg,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("database access must not occur")
        ),
    )

    assert preflight.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "code": "migration_preflight_failed",
        "result": "failed",
    }
