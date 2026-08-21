from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from infra import api_only_secret_live_guard as guard

SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"


def _query() -> dict[str, str]:
    return {
        "resource_group": "cosmaneura-prod",
        "subscription_id": SUBSCRIPTION,
    }


def _resource(*, api: bool, literal: bool = False, duplicate: bool = False) -> str:
    environment = []
    secrets = [{"name": "registry-secret"}]
    if api:
        entry = {
            "name": guard.ENV_NAME,
            "secretRef": guard.SECRET_NAME,
        }
        if literal:
            entry["value"] = "never-print-this-secret"
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

    assert guard.verify_live_state(_query(), runner=runner) == len(guard.WORKLOADS)

    for leaked_name in (
        "cosmaneura-prod-worker",
        "cosmaneura-prod-scheduler",
        "cosmaneura-prod-app-jmon",
        "cosmaneura-prod-migrate",
    ):

        def leaked_runner(arguments: tuple[str, ...], leaked=leaked_name) -> str:
            name = arguments[arguments.index("--name") + 1]
            return _resource(api=name in {"cosmaneura-prod-app", leaked})

        with pytest.raises(guard.LiveGuardError, match="non-API"):
            guard.verify_live_state(_query(), runner=leaked_runner)


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
        guard.verify_live_state(_query(), runner=runner)


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
    monkeypatch.setattr(guard.subprocess, "run", fake_run)

    with pytest.raises(guard.LiveGuardError, match="returned a failure") as error:
        guard._run_az(("containerapp", "show"))

    assert guard.ENV_NAME not in captured["env"]
    assert "never-print-this-secret" not in str(error.value)


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
