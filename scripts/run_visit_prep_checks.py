"""Run Visit Prep checks against a new, owned PostgreSQL cluster.

Never load local environment files or the repository-wide pytest conftest:
that hook can delete shared Clerk test users and truncate an existing database.
All configuration passed to the child is invented, and Python network egress
is denied by the isolated pytest plugin. Requires local PostgreSQL 16 tools.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTS = (
    "tests/migrations_graph_test.py",
    "tests/visit_prep_question_set_migration_test.py",
    "tests/services/visit_prep_question_set_model_test.py",
    "tests/services/visit_prep_question_definitions_test.py",
    "tests/services/visit_prep_access_test.py",
    "tests/routes/visit_prep_dependency_test.py",
)


def synthetic_environment() -> dict[str, str]:
    """Reuse the authority suite's literal synthetic defaults without executing it."""
    source = ROOT / "tests/authority_kernel/conftest.py"
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_TEST_ENVIRONMENT"
            for target in node.targets
        ):
            values = ast.literal_eval(node.value)
            if not isinstance(values, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in values.items()
            ):
                raise RuntimeError("synthetic test configuration is not a string map")
            return values
    raise RuntimeError("authority synthetic test configuration was not found")


def _executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"local {name} is required")
    return executable


def main() -> int:
    commands = {name: _executable(name) for name in ("initdb", "pg_ctl", "createdb")}
    environment = {
        "PATH": os.defpath + ":/opt/homebrew/bin:/usr/local/bin",
        "LC_ALL": "C",
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        **synthetic_environment(),
    }
    arguments = sys.argv[1:]
    export_openapi = bool(arguments and arguments[0] == "--export-openapi")
    if export_openapi:
        arguments = arguments[1:]
        environment["VISIT_PREP_EXPORT_OPENAPI"] = "1"
    tests = arguments or list(DEFAULT_TESTS)
    # Only repository test paths are accepted, never additional pytest plugins
    # or configuration flags capable of reconnecting to another environment.
    for name in tests:
        candidate = (ROOT / name.split("::", 1)[0]).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(ROOT / "tests"):
            raise ValueError("arguments must identify existing repository tests")
    with tempfile.TemporaryDirectory(
        prefix="cosma-vp-tests-", dir="/private/tmp"
    ) as tmp:
        owned = Path(tmp)
        database = owned / "data"
        socket_dir = owned / "socket"
        socket_dir.mkdir(mode=0o700)
        environment["TMPDIR"] = str(owned)
        environment["VISIT_PREP_OWNED_TEST_DIRECTORY"] = str(owned)
        environment["TEST_DATABASE_URL"] = (
            f"postgresql://visit_prep_test_owner@/visit_prep_test?host={socket_dir}"
        )
        started = False
        try:
            subprocess.run(
                [
                    commands["initdb"],
                    "-D",
                    str(database),
                    "-U",
                    "visit_prep_test_owner",
                    "--auth-local=trust",
                    "--auth-host=reject",
                    "--no-locale",
                    "-E",
                    "UTF8",
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            started = True
            subprocess.run(
                [
                    commands["pg_ctl"],
                    "-D",
                    str(database),
                    "-l",
                    str(owned / "postgres.log"),
                    "-o",
                    f"-c listen_addresses='' -k {socket_dir}",
                    "-w",
                    "-t",
                    "20",
                    "start",
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=25,
            )
            subprocess.run(
                [
                    commands["createdb"],
                    "-h",
                    str(socket_dir),
                    "-U",
                    "visit_prep_test_owner",
                    "visit_prep_test",
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            print(
                "Visit Prep: owned synthetic PostgreSQL; no shared service credentials."
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--noconftest",
                    "-p",
                    "tests.visit_prep.isolated_plugin",
                    "-p",
                    "pytest_asyncio.plugin",
                    "-q",
                    "--tb=short",
                    *tests,
                ],
                cwd=ROOT,
                env=environment,
                timeout=600,
            )
            if export_openapi and result.returncode == 0:
                output = ROOT / "tmp/visit-prep-internal-openapi.json"
                output.parent.mkdir(exist_ok=True)
                shutil.copyfile(owned / "internal-openapi.json", output)
                print(f"Visit Prep: generated internal OpenAPI at {output}")
            return result.returncode
        except subprocess.CalledProcessError as error:
            # Only our new, synthetic cluster writes this diagnostic file.
            print(error.stdout or "", file=sys.stderr)
            print(error.stderr or "", file=sys.stderr)
            log = owned / "postgres.log"
            if log.is_file():
                print(log.read_text(), file=sys.stderr)
            raise
        finally:
            if started and (database / "postmaster.pid").exists():
                subprocess.run(
                    [
                        commands["pg_ctl"],
                        "-D",
                        str(database),
                        "-w",
                        "-t",
                        "20",
                        "-m",
                        "immediate",
                        "stop",
                    ],
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=25,
                )
                print(
                    "Visit Prep: owned PostgreSQL stopped; disposable synthetic files removed."
                )


if __name__ == "__main__":
    raise SystemExit(main())
