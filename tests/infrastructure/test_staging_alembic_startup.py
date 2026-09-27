from __future__ import annotations

import ast
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import CommandLine, Config

from sqlalchemy import create_engine, engine_from_config

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/migration_job_template_guard.py"
SPEC = importlib.util.spec_from_file_location("staging_migration_guard", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


@pytest.mark.parametrize(
    ("password", "certificate", "decoded_password"),
    [
        ("synthetic%40pass", "%2Fetc%2Fssl%2Fcerts%2Fca.pem", "synthetic@pass"),
        ("synthetic-safe", "/etc/ssl/certs/ca.pem", "synthetic-safe"),
    ],
)
@pytest.mark.parametrize("release_v2", [False, True])
def test_actual_job_preserves_startup_url_then_escapes_only_alembic_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    password: str,
    certificate: str,
    decoded_password: str,
    release_v2: bool,
) -> None:
    raw = (
        f"postgresql+psycopg://synthetic:{password}@db.example.test/staging"
        f"?sslmode=verify-full&sslrootcert={certificate}"
    )
    deploy = (ROOT / "infra-staging/deploy.sh").read_text()
    start = deploy.index("run_migrations() {")
    function = deploy[start : deploy.index("\n}", start) + 2]
    capture = tmp_path / "create-arguments.json"
    script = r"""
set -eu
GIT_SHA=866bd4bc01e0e7be32c59eccdaf9dac3f495c7de
FOUNDER_CANDIDATE_SHA=$GIT_SHA
STAGING_IMAGE_DIGEST=sha256:6fa75d4093590b83dc2f50962f1979481a8c3d3d9d1dd9f647b7f407de0690a5
FOUNDER_CANDIDATE_IMAGE_DIGEST=$STAGING_IMAGE_DIGEST
STAGING_IMAGE_REFERENCE="registry.example/test@$STAGING_IMAGE_DIGEST"
MIGRATION_JOB=cosmaneura-staging-migrate
RESOURCE_GROUP=cosmaneura-staging
ENVIRONMENT_NAME=synthetic-environment
ACR_SERVER=registry.example
ACR_USERNAME=synthetic
ACR_PASSWORD=synthetic
ALEMBIC_COMPAT_DATABASE_SECRET_NAME=database-url-alembic-compat
COMMON_SECRET_VARS=(DATABASE_URL)
PUBLIC_VARS=(AUTO_MIGRATE_ON_STARTUP)
az() {
    case "$1 $2 $3" in
        "containerapp job show") return 1 ;;
        "containerapp job list") echo 0 ;;
        "containerapp job create")
            "$PYTHON_BIN" -c 'import json,os,sys;open(os.environ["CAPTURE"],"w").write(json.dumps(sys.argv[1:]))' "$@"
            return 1 ;;
        *) return 99 ;;
    esac
}
"""
    test_env = {
        "PATH": os.environ["PATH"],
        "PYTHON_BIN": sys.executable,
        "SCRIPT_DIR": str(ROOT / "infra-staging"),
        "ENV_JSON": json.dumps(
            {"DATABASE_URL": raw, "AUTO_MIGRATE_ON_STARTUP": "false"}
        ),
        "CAPTURE": str(capture),
        "RELEASE_V2_JSON": '{"version":2}' if release_v2 else "",
    }
    result = subprocess.run(
        ["bash", "-c", script + function + "\nrun_migrations\n"],
        env=test_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1  # Stop at the captured create; never run a job.
    assert raw not in result.stdout + result.stderr
    arguments = json.loads(capture.read_text())
    assert f"database-url={raw}" in arguments
    assert f"database-url-alembic-compat={raw}" in arguments
    assert "DATABASE_URL=secretref:database-url-alembic-compat" in arguments
    assert "AUTO_MIGRATE_ON_STARTUP=false" in arguments
    command = arguments[arguments.index("--args") + 1]
    assert (
        command == (GUARD.ALEMBIC_ARGS if release_v2 else GUARD.STAGING_ALEMBIC_ARGS)[0]
    )
    helper_start = deploy.index("verify_migration_job_template() {")
    helper = deploy[helper_start : deploy.index("\n}", helper_start) + 2]
    template = {
        "containers": [
            {
                "name": "cosmaneura-staging-migrate",
                "image": "registry.example/test@"
                + "sha256:6fa75d4093590b83dc2f50962f1979481a8c3d3d9d1dd9f647b7f407de0690a5",
                "command": None,
                "args": [command],
                "env": [
                    {
                        "name": "DATABASE_URL",
                        "secretRef": "database-url-alembic-compat",
                    },
                    {"name": "AUTO_MIGRATE_ON_STARTUP", "secretRef": None},
                ],
            }
        ],
        "secretNames": ["database-url", "database-url-alembic-compat"],
    }
    provider = '\naz() { printf "%s" "$SYNTHETIC_TEMPLATE"; }\n'
    for wrong_command in (False, True):
        if wrong_command:
            template["containers"][0]["args"] = [
                (GUARD.STAGING_ALEMBIC_ARGS if release_v2 else GUARD.ALEMBIC_ARGS)[0]
            ]
        verified = subprocess.run(
            [
                "bash",
                "-c",
                script + provider + helper + "\nverify_migration_job_template\n",
            ],
            env={**test_env, "SYNTHETIC_TEMPLATE": json.dumps(template)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert (verified.returncode != 0) is wrong_command
    invocation = shlex.split(command)
    if release_v2:
        assert invocation == ["/opt/venv/bin/alembic", "upgrade", "head"]
    else:
        assert invocation[:2] == ["/opt/venv/bin/python", "-c"]
        assert len(invocation) == 3

    # Model the real candidate's order with both real URL consumers: app
    # startup asks the driver for connection arguments before env.py sets URL.
    monkeypatch.setenv("DATABASE_URL", raw)
    monkeypatch.setattr(Config, "set_main_option", Config.set_main_option)
    observed = []

    def migration_main(self: CommandLine, argv: list[str]) -> None:
        assert argv == ["upgrade", "head"]
        startup_engine = create_engine(os.environ["DATABASE_URL"])
        startup = startup_engine.dialect.create_connect_args(startup_engine.url)[1]
        assert startup["password"] == decoded_password
        assert startup["sslmode"] == "verify-full"
        assert startup["sslrootcert"] == "/etc/ssl/certs/ca.pem"

        config = Config()
        if release_v2:
            # Execute the actual candidate's configuration expression, then
            # inspect real driver arguments; no database credentials or I/O.
            tree = ast.parse((ROOT / "migrations/env.py").read_text())
            statements: list[ast.stmt] = [
                node
                for node in tree.body
                if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "config"
                and node.value.func.attr == "set_main_option"
            ]
            assert len(statements) == 1
            exec(
                compile(
                    ast.Module(body=statements, type_ignores=[]),
                    "candidate-migration-configuration",
                    "exec",
                ),
                {"config": config, "database_url": lambda: raw},
            )
        else:
            config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
        assert config.get_main_option("sqlalchemy.url") == raw
        section = config.get_section(config.config_ini_section)
        assert section is not None
        migration_engine = engine_from_config(section, prefix="sqlalchemy.")
        migration = migration_engine.dialect.create_connect_args(migration_engine.url)[
            1
        ]
        # Each engine creates its own psycopg adapter context.
        assert {key: value for key, value in migration.items() if key != "context"} == {
            key: value for key, value in startup.items() if key != "context"
        }
        assert os.environ["DATABASE_URL"] == raw
        with pytest.raises(ValueError):
            config.set_main_option("unrelated.option", "invalid%interpolation")
        observed.append(True)

    monkeypatch.setattr(CommandLine, "main", migration_main)
    if release_v2:
        CommandLine().main(argv=invocation[1:])
    else:
        exec(compile(invocation[2], "staging-alembic-bootstrap", "exec"), {})
    assert observed == [True]


@pytest.mark.parametrize("release_v2", [False, True])
def test_staging_command_cli_emits_the_exact_guarded_command(release_v2: bool) -> None:
    flag = "--alembic-command" if release_v2 else "--staging-alembic-command"
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), flag],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert (
        result.stdout.strip()
        == (GUARD.ALEMBIC_ARGS if release_v2 else GUARD.STAGING_ALEMBIC_ARGS)[0]
    )
