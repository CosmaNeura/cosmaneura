from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from infra import api_only_secret_live_guard as guard

SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"
VALID_SIGNING_SECRET = "whsec_dGVzdC1vbmx5LWtleS0xNg=="


def _query() -> dict[str, str]:
    return {
        "resource_group": "cosmaneura-prod",
        "subscription_id": SUBSCRIPTION,
    }


def _resource(
    *,
    api: bool,
    literal: bool = False,
    azure_empty_value: bool = False,
    duplicate: bool = False,
) -> str:
    environment = []
    secrets = [{"name": "registry-secret"}]
    if api:
        entry = {
            "name": guard.ENV_NAME,
            "secretRef": guard.SECRET_NAME,
        }
        if literal:
            entry["value"] = "never-print-this-secret"
        elif azure_empty_value:
            # Azure normalizes secretRef-backed env entries with value="".
            entry["value"] = ""
        environment.append(entry)
        secrets.append({"name": guard.SECRET_NAME})
        if duplicate:
            environment.append(dict(entry))
    return json.dumps(
        {
            "properties": {
                "configuration": {"secrets": secrets},
                "template": {"containers": [{"env": environment}]},
            }
        }
    )


def test_live_scope_requires_exactly_one_api_reference_and_no_leakage() -> None:
    def runner(arguments: tuple[str, ...]) -> str:
        name = arguments[arguments.index("--name") + 1]
        return _resource(api=name == "cosmaneura-prod-app")

    assert guard.verify_live_state(
        _query(), runner=runner, signing_secret=VALID_SIGNING_SECRET
    ) == len(guard.WORKLOADS)

    for leaked_name in (
        "cosmaneura-prod-worker",
        "cosmaneura-prod-scheduler",
        "cosmaneura-prod-migrate",
    ):

        def leaked_runner(arguments: tuple[str, ...], leaked=leaked_name) -> str:
            name = arguments[arguments.index("--name") + 1]
            is_bound = name in {"cosmaneura-prod-app", leaked}
            return _resource(api=is_bound, azure_empty_value=is_bound)

        with pytest.raises(guard.LiveGuardError, match="non-API"):
            guard.verify_live_state(
                _query(), runner=leaked_runner, signing_secret=VALID_SIGNING_SECRET
            )


def test_live_scope_accepts_azure_normalized_empty_value_on_api_reference() -> None:
    def runner(arguments: tuple[str, ...]) -> str:
        name = arguments[arguments.index("--name") + 1]
        return _resource(
            api=name == "cosmaneura-prod-app",
            azure_empty_value=name == "cosmaneura-prod-app",
        )

    assert guard.verify_live_state(
        _query(), runner=runner, signing_secret=VALID_SIGNING_SECRET
    ) == len(guard.WORKLOADS)


def test_live_scope_accepts_azure_normalized_null_value_on_api_reference() -> None:
    api_resource = json.loads(_resource(api=True))
    api_resource["properties"]["template"]["containers"][0]["env"][0]["value"] = None

    def runner(arguments: tuple[str, ...]) -> str:
        name = arguments[arguments.index("--name") + 1]
        return (
            json.dumps(api_resource)
            if name == "cosmaneura-prod-app"
            else _resource(api=False)
        )

    assert guard.verify_live_state(
        _query(), runner=runner, signing_secret=VALID_SIGNING_SECRET
    ) == len(guard.WORKLOADS)


@pytest.mark.parametrize("secret_ref", [None, "wrong-secret"])
def test_live_scope_rejects_missing_or_wrong_api_secret_reference(
    secret_ref: str | None,
) -> None:
    api_resource = json.loads(_resource(api=True, azure_empty_value=True))
    entry = api_resource["properties"]["template"]["containers"][0]["env"][0]
    if secret_ref is None:
        entry.pop("secretRef")
    else:
        entry["secretRef"] = secret_ref

    def runner(arguments: tuple[str, ...]) -> str:
        name = arguments[arguments.index("--name") + 1]
        return (
            json.dumps(api_resource)
            if name == "cosmaneura-prod-app"
            else _resource(api=False)
        )

    with pytest.raises(guard.LiveGuardError, match="secret"):
        guard.verify_live_state(
            _query(), runner=runner, signing_secret=VALID_SIGNING_SECRET
        )


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_live_scope_rejects_missing_or_duplicate_api_secret_object(mode: str) -> None:
    api_resource = json.loads(_resource(api=True, azure_empty_value=True))
    secrets = api_resource["properties"]["configuration"]["secrets"]
    if mode == "missing":
        secrets[:] = [item for item in secrets if item["name"] != guard.SECRET_NAME]
    else:
        secrets.append({"name": guard.SECRET_NAME})

    def runner(arguments: tuple[str, ...]) -> str:
        name = arguments[arguments.index("--name") + 1]
        return (
            json.dumps(api_resource)
            if name == "cosmaneura-prod-app"
            else _resource(api=False)
        )

    with pytest.raises(guard.LiveGuardError, match="secret"):
        guard.verify_live_state(
            _query(), runner=runner, signing_secret=VALID_SIGNING_SECRET
        )


def test_live_scope_rejects_non_api_backing_secret_without_env_binding() -> None:
    leaked_resource = json.loads(_resource(api=False))
    leaked_resource["properties"]["configuration"]["secrets"].append(
        {"name": guard.SECRET_NAME}
    )

    def runner(arguments: tuple[str, ...]) -> str:
        name = arguments[arguments.index("--name") + 1]
        if name == "cosmaneura-prod-app":
            return _resource(api=True, azure_empty_value=True)
        if name == "cosmaneura-prod-worker":
            return json.dumps(leaked_resource)
        return _resource(api=False)

    with pytest.raises(guard.LiveGuardError, match="non-API"):
        guard.verify_live_state(
            _query(), runner=runner, signing_secret=VALID_SIGNING_SECRET
        )


@pytest.mark.parametrize(
    "api_resource",
    [
        _resource(api=False),
        _resource(api=True, literal=True),
        _resource(api=True, duplicate=True),
    ],
    ids=["missing", "literal", "duplicate"],
)
def test_live_scope_rejects_missing_literal_or_duplicate_api_binding(
    api_resource: str,
) -> None:
    def runner(arguments: tuple[str, ...]) -> str:
        name = arguments[arguments.index("--name") + 1]
        return api_resource if name == "cosmaneura-prod-app" else _resource(api=False)

    with pytest.raises(guard.LiveGuardError, match="API secret reference"):
        guard.verify_live_state(
            _query(), runner=runner, signing_secret=VALID_SIGNING_SECRET
        )


@pytest.mark.parametrize(
    "signing_secret",
    [
        "",
        "not-whsec",
        "whsec_",
        "whsec_not-base64!",
        " whsec_dGVzdA==",
        "whsec_dGVzdA==",
        "whsec_c3RhdGljLXRlc3Qta2V5",
        "whsec_AB==",
        "whsec_AAB=",
    ],
)
def test_live_scope_rejects_invalid_signing_secret_without_echoing_it(
    signing_secret: str,
) -> None:
    with pytest.raises(guard.LiveGuardError) as error:
        guard.verify_live_state(
            _query(),
            runner=lambda _arguments: _resource(api=False),
            signing_secret=signing_secret,
        )

    if signing_secret:
        assert signing_secret not in str(error.value)


def test_azure_invocation_strips_secret_and_never_returns_stderr(monkeypatch) -> None:
    captured = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=1,
            stdout="never-print-this-secret",
            stderr="never-print-this-secret",
        )

    monkeypatch.setenv(guard.ENV_NAME, "never-print-this-secret")
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "never-print-this-op-token")
    monkeypatch.setattr(guard.subprocess, "run", fake_run)

    with pytest.raises(guard.LiveGuardError, match="returned a failure") as error:
        guard._run_az(("containerapp", "show"))

    assert guard.ENV_NAME not in captured["env"]
    assert "OP_SERVICE_ACCOUNT_TOKEN" not in captured["env"]
    assert "never-print-this-secret" not in str(error.value)
    assert "never-print-this-op-token" not in str(error.value)


def test_main_reports_only_sanitized_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(json.dumps(_query())))
    monkeypatch.setattr(
        guard,
        "verify_live_state",
        lambda _query: (_ for _ in ()).throw(
            guard.LiveGuardError("never-print-this-secret")
        ),
    )

    assert guard.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"code": "api_only_secret_live_guard_failed"}
    assert "never-print-this-secret" not in output.err


@pytest.mark.parametrize(
    ("signing_secret", "expected_returncode"),
    [
        (VALID_SIGNING_SECRET, 0),
        ("whsec_dGVzdA==", 1),
        ("whsec_c3RhdGljLXRlc3Qta2V5", 1),
        ("whsec_not-base64!", 1),
        (" whsec_dGVzdC1vbmx5LWtleS0xNg==", 1),
    ],
)
def test_signing_secret_only_cli_is_canonical_and_value_free(
    signing_secret: str, expected_returncode: int
) -> None:
    environment = os.environ.copy()
    environment[guard.ENV_NAME] = signing_secret
    result = subprocess.run(
        [
            sys.executable,
            str(guard.__file__),
            "--validate-signing-secret-only",
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == expected_returncode
    assert signing_secret not in result.stdout
    assert signing_secret not in result.stderr
