"""Synthetic-only bootstrap used by scripts/run_visit_prep_checks.py.

The runner creates a fresh Unix-socket-only PostgreSQL cluster. No shared
database reset, Clerk cleanup, shared Redis connection or provider call is allowed.
Optional real Redis checks use a new networkless container through its local CLI.
Application models and migrations are real, not substitute test schemas.
"""

import importlib
import json
import os
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.visit_prep.owned_redis import (
    owned_rate_limit_redis as owned_rate_limit_redis,
)


def pytest_sessionstart(session):
    root = Path(os.environ["VISIT_PREP_OWNED_TEST_DIRECTORY"])
    if (
        root.parent != Path("/private/tmp")
        or not root.name.startswith("cosma-vp-tests-")
        or not (root / "data/postmaster.pid").is_file()
        or os.environ.get("PYTHON_ENV") != "test"
        or os.environ.get("TEST_DATABASE_URL")
        != (
            "postgresql://visit_prep_test_owner@/visit_prep_test"
            f"?host={root / 'socket'}"
        )
    ):
        raise RuntimeError("Visit Prep tests require the owned synthetic runner")

    guard = pytest.MonkeyPatch()

    def deny_network(*_args, **_kwargs):
        raise RuntimeError("network access is disabled in Visit Prep tests")

    guard.setattr(socket.socket, "connect", deny_network)
    guard.setattr(socket.socket, "connect_ex", deny_network)
    session.config._visit_prep_network_guard = guard

    # Application import ordinarily probes PostgreSQL AND Redis. The owned
    # PostgreSQL is exercised by real migrations below; Redis is not used by
    # the question-authoring slice. Restore the real module after bootstrap.
    name = "app.configuration.versions"
    versions = ModuleType(name)
    setattr(versions, "check_service_versions", lambda: None)
    sys.modules[name] = versions
    try:
        import app  # noqa: F401 -- application setup precedes real migrations
        from app.configuration.database import run_migrations

        run_migrations()
    finally:
        sys.modules.pop(name, None)
        real_versions = importlib.import_module(name)
        if "app" in sys.modules:
            setattr(
                sys.modules["app"],
                "check_service_versions",
                real_versions.check_service_versions,
            )


def pytest_sessionfinish(session):
    if migration_name := os.environ.get("VISIT_PREP_GENERATE_MIGRATION"):
        if session.exitstatus == 0:
            from alembic import command
            from alembic.config import Config

            command.revision(
                Config("alembic.ini"), message=migration_name, autogenerate=True
            )
    if os.environ.get("VISIT_PREP_EXPORT_OPENAPI") == "1" and session.exitstatus == 0:
        from app.routes.internal import internal_api_app
        from app.utils.openapi import generate_openapi_schema

        output = (
            Path(os.environ["VISIT_PREP_OWNED_TEST_DIRECTORY"])
            / "internal-openapi.json"
        )
        output.write_text(json.dumps(generate_openapi_schema(internal_api_app)))
    if "activemodel.session_manager" in sys.modules:
        from activemodel.session_manager import SessionManager

        SessionManager.get_instance().get_engine().dispose()
    guard = getattr(session.config, "_visit_prep_network_guard", None)
    if guard is not None:
        guard.undo()


@pytest.fixture(autouse=True)
def isolated_database_test(request):
    if "integration" in request.node.path.parts:
        # Independent-connection races need real commits; their factories use
        # unique invented owners. The entire owned cluster is discarded later.
        yield
    else:
        from activemodel.pytest import database_reset_transaction

        yield from database_reset_transaction()
