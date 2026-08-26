import importlib.util
import io
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from infra import deid_managed_identity_live_guard as guard

ROOT = Path(__file__).parents[2]
AZURE_CONFIGURATION = ROOT / "app" / "configuration" / "azure.py"
SUBSCRIPTION_ID = "00000000-0000-0000-0000-000000000000"
RESOURCE_GROUP = "cosmaneura-prod"
IDENTITY_NAME = "cosmaneura-prod-health-services"
WORKER_NAME = "cosmaneura-prod-worker"
SERVICE_NAME = "AnonymizerAPI"
CLIENT_ID = "11111111-1111-1111-1111-111111111111"
PRINCIPAL_ID = "22222222-2222-2222-2222-222222222222"
IDENTITY_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{IDENTITY_NAME}"
)
DEID_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/{guard.DEID_RESOURCE_TYPE}/{SERVICE_NAME}"
)


def _load_azure_configuration():
    spec = importlib.util.spec_from_file_location(
        "isolated_deid_azure_configuration", AZURE_CONFIGURATION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _set_base_azure_environment(monkeypatch) -> None:
    values = {
        "AZURE_UPLOADS_ACCOUNT_NAME": "test",
        "AZURE_UPLOADS_ACCOUNT_KEY": "test",
        "AZURE_UPLOADS_ACCOUNT_URL": "https://example.invalid",
        "AZURE_UPLOADS_CONTAINER_NAME": "test",
        "AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME": "test",
        "AZURE_DEID_ENDPOINT": "https://example.invalid",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_managed_identity_configuration_does_not_require_legacy_secret(
    monkeypatch,
) -> None:
    _set_base_azure_environment(monkeypatch)
    monkeypatch.setenv("AZURE_DEID_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID", CLIENT_ID)
    for name in (
        "AZURE_DEID_TENANT_ID",
        "AZURE_DEID_CLIENT_ID",
        "AZURE_DEID_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    configured = _load_azure_configuration()

    assert configured.AZURE_DEID_AUTH_MODE == "managed_identity"
    assert configured.AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID == CLIENT_ID
    assert configured.AZURE_DEID_CLIENT_SECRET == ""


def test_managed_identity_configuration_rejects_non_uuid_client_id(
    monkeypatch,
) -> None:
    _set_base_azure_environment(monkeypatch)
    monkeypatch.setenv("AZURE_DEID_AUTH_MODE", "managed_identity")
    monkeypatch.setenv("AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID", "not-a-uuid")

    with pytest.raises(
        ValueError,
        match="AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID must be a canonical UUID",
    ):
        _load_azure_configuration()


def test_legacy_configuration_remains_the_default_rollback(monkeypatch) -> None:
    _set_base_azure_environment(monkeypatch)
    monkeypatch.delenv("AZURE_DEID_AUTH_MODE", raising=False)
    monkeypatch.delenv("AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID", raising=False)
    monkeypatch.setenv("AZURE_DEID_TENANT_ID", SUBSCRIPTION_ID)
    monkeypatch.setenv("AZURE_DEID_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AZURE_DEID_CLIENT_SECRET", "synthetic-not-a-secret")

    configured = _load_azure_configuration()

    assert configured.AZURE_DEID_AUTH_MODE == "client_secret"
    assert configured.AZURE_DEID_CLIENT_ID == CLIENT_ID
    assert configured.AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID == ""


def test_configuration_rejects_ambiguous_auth_mode(monkeypatch) -> None:
    _set_base_azure_environment(monkeypatch)
    monkeypatch.setenv("AZURE_DEID_AUTH_MODE", "automatic_fallback")

    with pytest.raises(ValueError, match="AZURE_DEID_AUTH_MODE"):
        _load_azure_configuration()


def _query(*, enabled: str = "true") -> dict[str, str]:
    return {
        "compatible_worker_image_tag": "a" * 40,
        "enabled": enabled,
        "expected_deid_endpoint": "https://deid.example.invalid",
        "identity_name": IDENTITY_NAME,
        "resource_group": RESOURCE_GROUP,
        "service_name": SERVICE_NAME,
        "subscription_id": SUBSCRIPTION_ID,
        "worker_name": WORKER_NAME,
    }


def _resources() -> dict[str, Any]:
    return {
        "identity": {
            "clientId": CLIENT_ID,
            "id": IDENTITY_ID,
            "principalId": PRINCIPAL_ID,
        },
        "resource": {
            "id": DEID_ID,
            "name": SERVICE_NAME,
            "properties": {"serviceUrl": "https://deid.example.invalid/"},
            "type": guard.DEID_RESOURCE_TYPE,
        },
        "worker": {
            "identity": {"userAssignedIdentities": {IDENTITY_ID: {}}},
            "properties": {
                "template": {
                    "containers": [
                        {
                            "env": [
                                {
                                    "name": guard.AUTH_MODE_ENV,
                                    "value": "client_secret",
                                },
                                {
                                    "name": guard.MANAGED_IDENTITY_CLIENT_ID_ENV,
                                    "value": CLIENT_ID,
                                },
                                {
                                    "name": "AZURE_DEID_ENDPOINT",
                                    "secretRef": "azure-deid-endpoint",
                                },
                            ]
                        }
                    ]
                }
            },
        },
        "revisions": [
            {
                "properties": {
                    "active": True,
                    "healthState": "Healthy",
                    "provisioningState": "Provisioned",
                    "template": {
                        "containers": [
                            {"image": f"registry.example/core:{'a' * 40}"}
                        ]
                    },
                }
            }
        ],
        "assignments": [
            {
                "principalId": PRINCIPAL_ID,
                "roleDefinitionName": guard.DEID_ROLE,
                "scope": DEID_ID,
            }
        ],
    }


def _runner(resources: dict[str, Any], calls: list[tuple[str, ...]]):
    def run(arguments: tuple[str, ...]) -> str:
        calls.append(arguments)
        if arguments[:2] == ("identity", "show"):
            return json.dumps(resources["identity"])
        if arguments[:2] == ("resource", "show"):
            return json.dumps(resources["resource"])
        if arguments[:2] == ("containerapp", "show"):
            return json.dumps(resources["worker"])
        if arguments[:3] == ("containerapp", "revision", "list"):
            return json.dumps(resources["revisions"])
        if arguments[:3] == ("role", "assignment", "list"):
            return json.dumps(resources["assignments"])
        raise AssertionError(f"unexpected command: {arguments}")

    return run


def test_disabled_gate_is_secretless_and_makes_no_azure_call() -> None:
    calls: list[tuple[str, ...]] = []

    result = guard.verify_live_state(
        _query(enabled="false"), runner=_runner(_resources(), calls)
    )

    assert result == {"check_count": "0", "result": "disabled"}
    assert calls == []


def test_activation_requires_exact_identity_resource_worker_and_role() -> None:
    calls: list[tuple[str, ...]] = []

    result = guard.verify_live_state(_query(), runner=_runner(_resources(), calls))

    assert result == {"check_count": "8", "result": "verified"}
    assert len(calls) == 5
    role_query = calls[-1]
    assert role_query[:3] == ("role", "assignment", "list")
    assert role_query[role_query.index("--scope") + 1] == DEID_ID
    assert "--include-inherited" in role_query
    assert role_query[role_query.index("--fill-principal-name") + 1] == "false"
    assert "--all" not in role_query


@pytest.mark.parametrize(
    "break_state",
    [
        lambda state: state["worker"].update({"identity": None}),
        lambda state: state["worker"]["identity"].update(
            {"userAssignedIdentities": {}}
        ),
        lambda state: state["worker"]["properties"]["template"]["containers"][0]["env"][
            1
        ].update({"value": "33333333-3333-3333-3333-333333333333"}),
        lambda state: state["worker"]["properties"]["template"]["containers"][0]["env"][
            2
        ].update({"secretRef": "unexpected-endpoint"}),
        lambda state: state["resource"]["properties"].update(
            {"serviceUrl": "https://other.example.invalid"}
        ),
        lambda state: state["revisions"][0]["properties"]["template"]["containers"][
            0
        ].update({"image": f"registry.example/core:{'b' * 40}"}),
        lambda state: state["revisions"][0]["properties"].update(
            {"healthState": "Degraded"}
        ),
        lambda state: state["assignments"][0].update(
            {"scope": f"/subscriptions/{SUBSCRIPTION_ID}"}
        ),
        lambda state: state["assignments"][0].update(
            {"roleDefinitionName": "DeID Data Owner"}
        ),
        lambda state: state["assignments"].append(
            {
                "principalId": PRINCIPAL_ID,
                "roleDefinitionName": "Reader",
                "scope": f"/subscriptions/{SUBSCRIPTION_ID}",
            }
        ),
    ],
)
def test_activation_fails_closed_on_identity_or_scope_drift(break_state: Any) -> None:
    resources = deepcopy(_resources())
    break_state(resources)

    with pytest.raises(guard.LiveGuardError):
        guard.verify_live_state(_query(), runner=_runner(resources, []))


def test_cli_failure_does_not_disclose_identifiers(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_query())))
    monkeypatch.setattr(
        guard,
        "_run_az",
        lambda _arguments: (_ for _ in ()).throw(guard.LiveGuardError("private")),
    )

    assert guard.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.err) == {"code": "deid_managed_identity_live_guard_failed"}
    assert SUBSCRIPTION_ID not in output.err
    assert CLIENT_ID not in output.err
    assert output.out == ""


def test_terraform_models_default_off_adoption_and_exact_least_privilege() -> None:
    main = (ROOT / "infra" / "main.tf").read_text()
    variables = (ROOT / "infra" / "variables.tf").read_text()

    assert (
        "default     = false"
        in variables[variables.index('variable "enable_deid_managed_identity"') :]
    )
    assert "identity_ids = [azurerm_user_assigned_identity.health_services.id]" in main
    assert 'name  = "AZURE_DEID_AUTH_MODE"' in main
    assert 'name  = "AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID"' in main
    assert "scope                = local.deid_service_resource_id" in main
    assert 'role_definition_name = "DeID Realtime Data User"' in main
    assert "data.external.deid_managed_identity_live_guard" in main
    assert "deid_managed_identity_compatible_worker_image_tag" in variables
