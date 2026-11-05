from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import pytest

from infra import database_url_live_guard as guard

SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"


def _query(*, selected: str = "database-url", enabled: str = "false") -> dict[str, str]:
    return {
        "resource_group": "cosmaneura-prod",
        "selected_secret": selected,
        "subscription_id": SUBSCRIPTION,
        "v2_enabled": enabled,
    }


def _resource(reference: str, *, include_v2: bool) -> dict[str, object]:
    secrets = [{"name": "database-url"}]
    if include_v2:
        secrets.append({"name": "database-url-v2"})
    return {
        "properties": {
            "configuration": {"secrets": secrets},
            "template": {
                "containers": [
                    {"env": [{"name": "DATABASE_URL", "secretRef": reference}]}
                ]
            },
        }
    }


def test_old_selector_allows_phase_a_before_v2_exists() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments: tuple[str, ...]) -> str:
        calls.append(arguments)
        return json.dumps(_resource("database-url", include_v2=False))

    states = guard.verify_live_state(_query(enabled="true"), runner=runner)

    assert len(states) == 4
    assert all(item["secret"] == "database-url" for item in states)
    assert all(
        "--subscription" in call
        and call[call.index("--subscription") + 1] == SUBSCRIPTION
        for call in calls
    )


def test_v2_selector_requires_prior_staged_move_and_both_secrets() -> None:
    def runner(arguments: tuple[str, ...]) -> str:
        return json.dumps(_resource("database-url-v2", include_v2=True))

    states = guard.verify_live_state(
        _query(selected="database-url-v2", enabled="true"), runner=runner
    )
    assert len(states) == 4

    with pytest.raises(guard.LiveGuardError, match="v2 secret is missing"):
        guard.verify_live_state(
            _query(selected="database-url-v2", enabled="true"),
            runner=lambda arguments: json.dumps(
                _resource("database-url-v2", include_v2=False)
            ),
        )


def test_mixed_live_selection_fails_closed() -> None:
    calls = 0

    def runner(arguments: tuple[str, ...]) -> str:
        nonlocal calls
        calls += 1
        reference = "database-url" if calls == 1 else "database-url-v2"
        return json.dumps(_resource(reference, include_v2=True))

    with pytest.raises(guard.LiveGuardError, match="adoption-only"):
        guard.verify_live_state(_query(), runner=runner)


def test_query_parser_rejects_unknown_fields_and_invalid_selector() -> None:
    with pytest.raises(guard.LiveGuardError, match="fields"):
        guard.read_query(io.StringIO('{"unexpected":"value"}'))
    with pytest.raises(guard.LiveGuardError, match="while disabled"):
        guard.validate_query(_query(selected="database-url-v2", enabled="false"))


@pytest.mark.parametrize("selected", ["database-url", "database-url-v2"])
def test_main_reports_only_the_validated_reference_name(
    selected: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    credential = "synthetic-database-credential-must-not-be-output"
    resource = _resource(selected, include_v2=True)
    # Azure responses may contain values as well as reference names.
    configuration: Any = resource["properties"]
    configuration["configuration"]["secrets"][0]["value"] = credential
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(command)
        assert "show" in command
        return SimpleNamespace(returncode=0, stdout=json.dumps(resource), stderr="")

    monkeypatch.setattr(guard.subprocess, "run", run)
    monkeypatch.setattr(
        guard.sys,
        "stdin",
        io.StringIO(json.dumps(_query(selected=selected, enabled="true"))),
    )

    assert guard.main() == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "result": "verified",
        "selected_secret": selected,
        "workload_count": "4",
    }
    assert len(calls) == 4
    assert credential not in captured.out


@pytest.mark.parametrize("failure", ["invalid-selector", "invalid-json", "azure-error"])
def test_main_failures_do_not_echo_input_or_azure_output(
    failure: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    credential = "synthetic-database-credential-must-not-be-output"
    query = _query()
    if failure == "invalid-selector":
        query["selected_secret"] = credential
    raw = credential if failure == "invalid-json" else json.dumps(query)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout=credential, stderr=credential)

    monkeypatch.setattr(guard.subprocess, "run", run)
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(raw))

    assert guard.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "code": "database_url_live_guard_failed",
        "result": "failed",
    }
    assert len(calls) == (1 if failure == "azure-error" else 0)
