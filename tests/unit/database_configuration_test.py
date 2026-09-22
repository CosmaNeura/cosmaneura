import importlib.util
import sys
import types
from pathlib import Path

import pytest
from alembic import command
from alembic import config as alembic_config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def database(monkeypatch):
    """Load the module without executing the application's global setup."""

    app_package = types.ModuleType("app")
    app_package.__path__ = [str(ROOT / "app")]
    setattr(
        app_package,
        "log",
        types.SimpleNamespace(info=lambda *_args, **_kwargs: None),
    )

    configuration_package = types.ModuleType("app.configuration")
    configuration_package.__path__ = [str(ROOT / "app" / "configuration")]

    environments = types.ModuleType("app.environments")
    setattr(environments, "is_development", lambda: False)
    setattr(environments, "is_production", lambda: False)
    setattr(environments, "is_staging", lambda: False)
    setattr(environments, "is_testing", lambda: True)

    setup = types.ModuleType("app.setup")
    setattr(setup, "get_root_path", lambda: ROOT)

    activemodel = types.ModuleType("activemodel")
    setattr(activemodel, "init", lambda _url: None)
    session_manager = types.ModuleType("activemodel.session_manager")
    setattr(session_manager, "get_engine", lambda: None)

    sqlmodel = types.ModuleType("sqlmodel")
    setattr(
        sqlmodel,
        "SQLModel",
        types.SimpleNamespace(
            metadata=types.SimpleNamespace(tables={}, create_all=lambda _engine: None)
        ),
    )

    for name, module in {
        "app": app_package,
        "app.configuration": configuration_package,
        "app.environments": environments,
        "app.setup": setup,
        "activemodel": activemodel,
        "activemodel.session_manager": session_manager,
        "sqlmodel": sqlmodel,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location(
        "app.configuration.database_for_test",
        ROOT / "app" / "configuration" / "database.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_startup_migrations_default_to_enabled(monkeypatch, database) -> None:
    observed = {}

    def fake_config(name, *, default, cast):
        observed.update(name=name, default=default, cast=cast)
        return default

    monkeypatch.setattr(database, "config", fake_config)

    assert database.should_auto_migrate_on_startup() is True
    assert observed == {
        "name": "AUTO_MIGRATE_ON_STARTUP",
        "default": True,
        "cast": bool,
    }


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_disabled_startup_migrations_do_not_run_for_runtime(
    monkeypatch, database, environment
) -> None:
    migration_calls = []
    initialized_urls = []

    monkeypatch.setenv("AUTO_MIGRATE_ON_STARTUP", "false")
    monkeypatch.setattr(database, "is_production", lambda: environment == "production")
    monkeypatch.setattr(database, "is_staging", lambda: environment == "staging")
    monkeypatch.setattr(
        database, "run_migrations", lambda: migration_calls.append(True)
    )
    monkeypatch.setattr(
        database, "database_url", lambda: "postgresql+psycopg://runtime"
    )
    monkeypatch.setattr(database.activemodel, "init", initialized_urls.append)

    database.configure_database()

    assert migration_calls == []
    assert initialized_urls == ["postgresql+psycopg://runtime"]


def test_enabled_startup_migrations_preserve_production_behavior(
    monkeypatch, database
) -> None:
    migration_calls = []

    monkeypatch.setenv("AUTO_MIGRATE_ON_STARTUP", "true")
    monkeypatch.setattr(database, "is_production", lambda: True)
    monkeypatch.setattr(database, "is_staging", lambda: False)
    monkeypatch.setattr(
        database, "run_migrations", lambda: migration_calls.append(True)
    )
    monkeypatch.setattr(
        database, "database_url", lambda: "postgresql+psycopg://runtime"
    )
    monkeypatch.setattr(database.activemodel, "init", lambda _url: None)

    database.configure_database()

    assert migration_calls == [True]


def test_direct_migration_entrypoint_is_not_controlled_by_startup_gate(
    monkeypatch,
    database,
) -> None:
    observed = {}

    class FakeConfig:
        def __init__(self, path):
            observed["config_path"] = path

    monkeypatch.setattr(
        database,
        "config",
        lambda *_args, **_kwargs: pytest.fail("startup gate read by direct migration"),
    )
    monkeypatch.setattr(database, "get_root_path", lambda: Path("/synthetic/app"))
    monkeypatch.setattr(alembic_config, "Config", FakeConfig)
    monkeypatch.setattr(
        command,
        "upgrade",
        lambda config, revision: observed.update(config=config, revision=revision),
    )

    database.run_migrations()

    assert observed["config_path"] == Path("/synthetic/app/alembic.ini")
    assert isinstance(observed["config"], FakeConfig)
    assert observed["revision"] == "head"
