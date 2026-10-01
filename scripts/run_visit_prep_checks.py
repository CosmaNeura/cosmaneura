"""Run Visit Prep checks against a new, owned PostgreSQL cluster.

Never load local environment files or the repository-wide pytest conftest:
that hook can delete shared Clerk test users and truncate an existing database.
All configuration passed to the child is invented, and Python network egress
is denied by the isolated pytest plugin. Requires local PostgreSQL 16 tools.
"""

from __future__ import annotations

import ast
import json
import os
import re
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
    with_redis = bool(arguments and arguments[0] == "--with-redis")
    if with_redis:
        arguments = arguments[1:]
    if arguments and arguments[0] == "--generate-migration":
        if len(arguments) < 2 or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", arguments[1]
        ):
            raise ValueError("migration generation requires a bounded lowercase name")
        environment["VISIT_PREP_GENERATE_MIGRATION"] = arguments[1]
        arguments = arguments[2:]
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
        redis_container: str | None = None
        docker: str | None = None
        docker_environment: dict[str, str] = {}
        try:
            if with_redis:
                docker = _executable("docker")
                endpoint = subprocess.run(
                    [
                        docker,
                        "context",
                        "inspect",
                        "--format",
                        "{{.Endpoints.docker.Host}}",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                ).stdout.strip()
                if not endpoint.startswith("unix://"):
                    raise RuntimeError(
                        "Visit Prep Redis tests require a local Docker socket"
                    )
                docker_environment = {
                    "PATH": environment["PATH"],
                    "DOCKER_HOST": endpoint,
                }
                image = subprocess.run(
                    [docker, "image", "inspect", "redis:6.2.18", "--format", "{{.Id}}"],
                    env=docker_environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                ).stdout.strip()
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                    raise RuntimeError(
                        "a locally cached Redis 6.2.18 image is required"
                    )
                # No pull, ports, host mounts or network; remove only this owned container.
                redis_container = owned.name + "-redis"
                subprocess.run(
                    [
                        docker,
                        "run",
                        "--detach",
                        "--rm",
                        "--pull=never",
                        "--name",
                        redis_container,
                        "--label",
                        f"cosma.visit-prep-test-owner={owned}",
                        "--network=none",
                        "--read-only",
                        "--cap-drop=ALL",
                        "--security-opt=no-new-privileges",
                        "--memory=64m",
                        "--pids-limit=64",
                        "--user=999:999",
                        "--entrypoint=redis-server",
                        "--workdir=/tmp",
                        "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
                        "--tmpfs=/data:rw,noexec,nosuid,size=1m",
                        image,
                        "--save",
                        "",
                        "--appendonly",
                        "no",
                        "--port",
                        "0",
                        "--unixsocket",
                        "/tmp/visit-prep-redis.sock",
                    ],
                    env=docker_environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                ready = False
                for _ in range(20):
                    ping = subprocess.run(
                        [
                            docker,
                            "exec",
                            redis_container,
                            "redis-cli",
                            "-s",
                            "/tmp/visit-prep-redis.sock",
                            "--raw",
                            "PING",
                        ],
                        env=docker_environment,
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if ping.returncode == 0 and ping.stdout.strip() == "PONG":
                        ready = True
                        break
                if not ready:
                    raise RuntimeError("owned Redis did not become ready")
                environment.update(
                    {
                        "VISIT_PREP_OWNED_REDIS_CONTAINER": redis_container,
                        "VISIT_PREP_TEST_DOCKER": docker,
                        "VISIT_PREP_TEST_DOCKER_HOST": endpoint,
                    }
                )
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
                    "--asyncio-mode=auto",
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
            # A Docker cleanup failure must not leave PostgreSQL running while
            # TemporaryDirectory removes its synthetic data directory.
            try:
                if redis_container is not None:
                    assert docker is not None
                    inspect = subprocess.run(
                        [docker, "inspect", redis_container],
                        env=docker_environment,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if inspect.returncode == 0:
                        objects = json.loads(inspect.stdout)
                        if len(objects) != 1 or objects[0]["Config"]["Labels"].get(
                            "cosma.visit-prep-test-owner"
                        ) != str(owned):
                            raise RuntimeError(
                                "refusing to stop a Redis container not owned by this run"
                            )
                        subprocess.run(
                            [docker, "stop", "--time=5", objects[0]["Id"]],
                            env=docker_environment,
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        print(
                            "Visit Prep: owned network-isolated Redis stopped and removed."
                        )
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
