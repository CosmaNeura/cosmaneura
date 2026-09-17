from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "billing_migration_preflight.py"


def _load_preflight() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_billing_migration_preflight", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResult:
    def __init__(self, row: tuple[Any, ...] | None):
        self.row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


class FakeConnection:
    def __init__(self, rows: Sequence[tuple[Any, ...] | None]):
        self.rows = iter(rows)
        self.queries: list[str] = []
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query: str) -> FakeResult:
        self.queries.append(query)
        return FakeResult(next(self.rows))

    def rollback(self) -> None:
        self.rolled_back = True


def test_preflight_import_is_isolated_from_app_and_alembic() -> None:
    probe = f"""
import importlib.util
import json
import sys
spec = importlib.util.spec_from_file_location('preflight_probe', {str(SCRIPT)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
forbidden = sorted(
    name for name in sys.modules
    if name == 'app' or name.startswith('app.')
    or name == 'alembic' or name.startswith('alembic.')
)
print(json.dumps(forbidden))
raise SystemExit(bool(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


def test_preflight_source_has_no_app_or_alembic_import() -> None:
    tree = ast.parse(SCRIPT.read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )

    assert not any(
        name == "app"
        or name.startswith("app.")
        or name == "alembic"
        or name.startswith("alembic.")
        for name in imported
    )


def test_queries_return_only_duplicate_group_counts() -> None:
    preflight = _load_preflight()
    connection = FakeConnection([(1, 1), (2,), (0,), (7,)])

    counts = preflight.collect_duplicate_group_counts(connection)

    assert counts == {
        "duplicate_stripe_customer_id_groups": 2,
        "duplicate_stripe_subscription_id_groups": 0,
        "duplicate_baa_acceptance_practice_version_groups": 7,
    }
    assert len(connection.queries) == 4
    presence_query = " ".join(connection.queries[0].lower().split())
    assert presence_query.startswith("select count(*) filter")
    assert "from information_schema.columns" in presence_query
    for query in connection.queries[1:]:
        normalized = " ".join(query.lower().split())
        assert normalized.startswith("select count(*) from (")
        assert "select 1" in normalized
        assert "having count(*) > 1" in normalized


def test_missing_pre_migration_stripe_columns_are_zero_risk() -> None:
    preflight = _load_preflight()
    connection = FakeConnection([(0, 0), (3,)])

    counts = preflight.collect_duplicate_group_counts(connection)

    assert counts == {
        "duplicate_stripe_customer_id_groups": 0,
        "duplicate_stripe_subscription_id_groups": 0,
        "duplicate_baa_acceptance_practice_version_groups": 3,
    }
    assert len(connection.queries) == 2
    assert "FROM practice" not in connection.queries[1]


@pytest.mark.parametrize(
    ("rows", "expected_exit"),
    [
        ([(1, 1), (0,), (0,), (0,)], 0),
        ([(1, 1), (0,), (1,), (0,)], 1),
        ([(0, 0), (0,)], 0),
    ],
)
def test_main_outputs_counts_and_fails_closed_on_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rows: list[tuple[int, ...]],
    expected_exit: int,
) -> None:
    preflight = _load_preflight()
    connection = FakeConnection(rows)
    connect_call: dict[str, Any] = {}

    def fake_connect(database_url: str, **kwargs: Any) -> FakeConnection:
        connect_call.update(database_url=database_url, **kwargs)
        return connection

    monkeypatch.setenv("DATABASE_URL", "postgresql://not-printed")
    monkeypatch.setattr(preflight.psycopg, "connect", fake_connect)

    assert preflight.main() == expected_exit
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert set(parsed) == {
        "duplicate_baa_acceptance_practice_version_groups",
        "duplicate_stripe_customer_id_groups",
        "duplicate_stripe_subscription_id_groups",
    }
    assert any(parsed.values()) is (expected_exit == 1)
    assert captured.err == ""
    assert connection.rolled_back
    assert connect_call["connect_timeout"] == 10
    assert "default_transaction_read_only=on" in connect_call["options"]
    assert "statement_timeout=15000" in connect_call["options"]


def test_main_never_echoes_connection_failure_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    preflight = _load_preflight()
    secret_dsn = "postgresql://operator:do-not-print@db.example/production"

    def fail_connect(*_args: Any, **_kwargs: Any):
        raise psycopg.OperationalError(f"could not connect to {secret_dsn}")

    monkeypatch.setenv("DATABASE_URL", secret_dsn)
    monkeypatch.setattr(preflight.psycopg, "connect", fail_connect)

    assert preflight.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "billing migration preflight failed\n"
    assert secret_dsn not in captured.err


def test_app_cli_does_not_offer_the_unsafe_preflight_command() -> None:
    cli_source = (ROOT / "app" / "cli" / "__init__.py").read_text()
    assert "billing-migration-preflight" not in cli_source


def test_preflight_script_is_packaged_in_the_application_image() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert SCRIPT.is_file()
    assert not any(
        line.strip() in {"scripts", "/scripts", "scripts/", "/scripts/"}
        for line in dockerignore
    )


def test_production_preflight_is_an_execution_override_before_alembic() -> None:
    deploy = (ROOT / "infra" / "deploy.sh").read_text()
    run_start = deploy.index("run_migrations()")
    reconcile = deploy.index("--reconcile-migration-secrets-stdin")
    preflight = deploy.index(
        "--args /app/scripts/billing_migration_preflight.py", reconcile
    )
    plain_start = deploy.index("local execution_name", preflight)

    assert reconcile < preflight < plain_start
    override = deploy[
        deploy.rfind("az containerapp job start", reconcile, preflight) : plain_start
    ]
    assert "properties.template.containers[].name" in deploy[run_start:preflight]
    assert (
        '[[ "$migration_container_name" != "$MIGRATION_JOB" ]]'
        in deploy[run_start:preflight]
    )
    assert '--container-name "$migration_container_name"' in override
    assert '--image "$PRODUCTION_TAG"' in override
    assert "--command /opt/venv/bin/python" in override
    assert '--env-vars "DATABASE_URL=secretref:$DATABASE_SECRET_NAME"' in override
    assert "Billing migration preflight blocked schema migration" in override
    assert "return 1" in override

    template_update = deploy[
        deploy.index("az containerapp job update") : deploy.index(
            "# The versioned database URL", reconcile - 3000
        )
    ]
    assert '--image "$PRODUCTION_TAG"' in template_update
    assert "--args" not in template_update


def test_staging_preflight_runs_after_job_creation_before_alembic() -> None:
    deploy = (ROOT / "infra-staging" / "deploy.sh").read_text()
    create = deploy.index("az containerapp job create")
    preflight = deploy.index(
        "--args /app/scripts/billing_migration_preflight.py", create
    )
    plain_start = deploy.index("local execution_name", preflight)

    assert create < preflight < plain_start
    assert '--args "/opt/venv/bin/alembic upgrade head"' in deploy[create:preflight]
    override = deploy[
        deploy.rfind("az containerapp job start", create, preflight) : plain_start
    ]
    assert "properties.template.containers[].name" in deploy[create:preflight]
    assert (
        '[[ "$migration_container_name" != "$MIGRATION_JOB" ]]'
        in deploy[create:preflight]
    )
    assert '--container-name "$migration_container_name"' in override
    assert '--image "$STAGING_IMAGE_REFERENCE"' in override
    assert "--command /opt/venv/bin/python" in override
    assert '--env-vars "DATABASE_URL=secretref:database-url"' in override
    assert "Billing migration preflight blocked schema migration" in override
    assert "return 1" in override


@pytest.mark.parametrize("path", ["infra/deploy.sh", "infra-staging/deploy.sh"])
def test_preflight_failure_and_timeout_block_migration(path: str) -> None:
    deploy = (ROOT / path).read_text()
    helper_start = deploy.index("wait_for_migration_job_execution()")
    helper_end = deploy.index("\n}\n\nrun_migrations()", helper_start)
    helper = deploy[helper_start:helper_end]

    assert "Failed|Stopped" in helper
    if path == "infra-staging/deploy.sh":
        assert 'echo "$phase remained nonterminal' in helper
        assert "az containerapp job stop" in helper
        assert helper.count("schema observation is unsafe") == 2
        assert helper.count("return 2") == 2
    else:
        assert 'echo "$phase timed out' in helper
    assert helper.count("return 1") >= 2
