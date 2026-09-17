from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migration_job_template_guard.py"
IMAGE = "registry.example/cosmaneura:" + "a" * 40
JOB_NAME = "cosmaneura-prod-migrate"
SECRET_NAMES = {"DATABASE_URL", "REDIS_URL"}
PUBLIC_NAMES = {"LOG_LEVEL"}
DATABASE_SECRET = "database-url-alembic-compat"
RAW_DATABASE_SECRET = "database-url"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_job_template_guard", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = _load_module()


def _payload() -> dict[str, object]:
    return {
        "containers": [
            {
                "name": JOB_NAME,
                "image": IMAGE,
                "command": None,
                "args": ["/opt/venv/bin/alembic upgrade head"],
                "env": [
                    {"name": "DATABASE_URL", "secretRef": DATABASE_SECRET},
                    {"name": "REDIS_URL", "secretRef": "redis-url"},
                    {"name": "LOG_LEVEL", "secretRef": None},
                ],
            }
        ],
        "secretNames": [RAW_DATABASE_SECRET, DATABASE_SECRET, "redis-url"],
    }


def _validate(payload: object) -> str | None:
    return GUARD.validate_template(
        payload,
        expected_name=JOB_NAME,
        expected_image=IMAGE,
        database_secret_name=DATABASE_SECRET,
        raw_database_secret_name=RAW_DATABASE_SECRET,
        secret_env_names=SECRET_NAMES,
        public_env_names=PUBLIC_NAMES,
    )


def test_exact_reviewed_alembic_template_is_accepted() -> None:
    assert _validate(_payload()) is None


def test_staging_requires_exact_bootstrap_and_production_keeps_plain_alembic() -> None:
    payload = _payload()

    def validate_staging() -> str | None:
        return GUARD.validate_template(
            payload,
            expected_name=JOB_NAME,
            expected_image=IMAGE,
            database_secret_name=DATABASE_SECRET,
            raw_database_secret_name=RAW_DATABASE_SECRET,
            secret_env_names=SECRET_NAMES,
            public_env_names=PUBLIC_NAMES,
            staging_alembic_compatibility=True,
        )

    assert validate_staging() == "persistent arguments"
    payload["containers"][0]["args"] = GUARD.STAGING_ALEMBIC_ARGS.copy()  # type: ignore[index]
    assert validate_staging() is None
    assert _validate(payload) == "persistent arguments"
    payload["containers"][0]["args"][0] += " --unexpected"  # type: ignore[index]
    assert validate_staging() == "persistent arguments"


def test_legacy_production_payload_remains_supported_without_raw_inventory() -> None:
    payload = _payload()
    assert (
        GUARD.validate_template(
            payload["containers"],
            expected_name=JOB_NAME,
            expected_image=IMAGE,
            database_secret_name=DATABASE_SECRET,
            raw_database_secret_name=None,
            secret_env_names=SECRET_NAMES,
            public_env_names=PUBLIC_NAMES,
        )
        is None
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("name", "wrong-job", "container identity"),
        ("image", "registry.example/cosmaneura:stale", "image identity"),
        ("command", ["/bin/sh"], "persistent command"),
        ("args", [], "persistent arguments"),
        ("args", ["/opt/venv/bin/alembic", "upgrade", "head"], "persistent arguments"),
        ("args", ["/opt/venv/bin/alembic downgrade base"], "persistent arguments"),
        (
            "args",
            ["/opt/venv/bin/alembic upgrade head && echo unsafe"],
            "persistent arguments",
        ),
    ],
)
def test_wrong_identity_image_or_execution_contract_is_rejected(
    field: str,
    value: object,
    reason: str,
) -> None:
    payload = _payload()
    payload["containers"][0][field] = value  # type: ignore[index]

    assert _validate(payload) == reason


@pytest.mark.parametrize(
    "payload",
    [None, {}, [], {"containers": [{}, {}], "secretNames": []}],
)
def test_malformed_or_ambiguous_container_shape_is_rejected(payload: object) -> None:
    assert _validate(payload) is not None


def test_database_reference_duplicate_and_unexpected_env_are_rejected() -> None:
    wrong_database = _payload()
    wrong_database["containers"][0]["env"][0]["secretRef"] = "wrong-database"  # type: ignore[index]
    assert _validate(wrong_database) == "database secret reference"

    duplicate = _payload()
    duplicate["containers"][0]["env"].append(  # type: ignore[index,union-attr]
        {"name": "LOG_LEVEL", "secretRef": None}
    )
    assert _validate(duplicate) == "duplicate environment identity"

    unexpected = _payload()
    unexpected["containers"][0]["env"].append(  # type: ignore[index,union-attr]
        {"name": "RECALL_WEBHOOK_SECRET", "secretRef": "recall-webhook-secret"}
    )
    assert _validate(unexpected) == "unexpected environment identity"


@pytest.mark.parametrize(
    "secret_names",
    [
        [],
        [RAW_DATABASE_SECRET],
        [DATABASE_SECRET],
        [RAW_DATABASE_SECRET, RAW_DATABASE_SECRET, DATABASE_SECRET],
        [RAW_DATABASE_SECRET, DATABASE_SECRET, DATABASE_SECRET],
        [RAW_DATABASE_SECRET, DATABASE_SECRET, None],
    ],
)
def test_raw_and_compatibility_database_secrets_are_required_once(
    secret_names: list[object],
) -> None:
    payload = _payload()
    payload["secretNames"] = secret_names

    assert _validate(payload) is not None


def test_raw_and_compatibility_database_secret_names_must_be_distinct() -> None:
    payload = _payload()
    assert (
        GUARD.validate_template(
            payload,
            expected_name=JOB_NAME,
            expected_image=IMAGE,
            database_secret_name=RAW_DATABASE_SECRET,
            raw_database_secret_name=RAW_DATABASE_SECRET,
            secret_env_names=SECRET_NAMES,
            public_env_names=PUBLIC_NAMES,
        )
        == "database secret identities"
    )


def test_secret_and_public_reference_types_are_enforced() -> None:
    literal_secret = _payload()
    literal_secret["containers"][0]["env"][1]["secretRef"] = None  # type: ignore[index]
    assert _validate(literal_secret) == "secret environment reference"

    secret_backed_public = _payload()
    secret_backed_public["containers"][0]["env"][2]["secretRef"] = "hidden-log-level"  # type: ignore[index]
    assert _validate(secret_backed_public) == "public environment reference"


def test_cli_rejects_invalid_provider_json_without_echoing_it() -> None:
    malformed = '{"secret":"must-not-echo"'
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--expected-name",
            JOB_NAME,
            "--expected-image",
            IMAGE,
            "--database-secret-name",
            DATABASE_SECRET,
            "--raw-database-secret-name",
            RAW_DATABASE_SECRET,
            "--secret-env-names",
            "DATABASE_URL,REDIS_URL",
        ],
        input=malformed,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must-not-echo" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("deploy_path", "configure_marker"),
    [
        ("infra/deploy.sh", "--reconcile-migration-secrets-stdin"),
        ("infra-staging/deploy.sh", "az containerapp job create"),
    ],
)
def test_template_guard_precedes_preflight_and_plain_migration_start(
    deploy_path: str,
    configure_marker: str,
) -> None:
    deploy = (ROOT / deploy_path).read_text(encoding="utf-8")
    helper_start = deploy.index("verify_migration_job_template()")
    helper_end = deploy.index("\n}\n", helper_start)
    helper = deploy[helper_start:helper_end]
    configure = deploy.index(configure_marker)
    first_guard = deploy.index("verify_migration_job_template || return 1", configure)
    preflight = deploy.index("/app/scripts/billing_migration_preflight.py", first_guard)
    second_guard = deploy.index("verify_migration_job_template || return 1", preflight)
    plain_start = deploy.index("local execution_name", preflight)

    assert configure < first_guard < preflight < second_guard < plain_start
    assert "migration_job_template_guard.py" in helper
    assert "name:name" in helper
    assert "image:image" in helper
    assert "command:command" in helper
    assert "args:args" in helper
    assert "secretRef:secretRef" in helper
    if deploy_path == "infra-staging/deploy.sh":
        assert "secretNames:" in helper
    assert "value:value" not in helper
    assert "Migration job template is not approved for schema mutation." in helper


def test_guard_cli_accepts_exact_value_minimized_payload() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--expected-name",
            JOB_NAME,
            "--expected-image",
            IMAGE,
            "--database-secret-name",
            DATABASE_SECRET,
            "--raw-database-secret-name",
            RAW_DATABASE_SECRET,
            "--secret-env-names",
            "DATABASE_URL,REDIS_URL",
            "--public-env-names",
            "LOG_LEVEL",
        ],
        input=json.dumps(_payload()),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
