import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "infra/scripts/reconcile-agent-api-role.sh"
TENANT_ID = "d51754d0-bba5-4810-820e-afac542844d6"
SUBSCRIPTION_ID = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
APP_OBJECT_ID = "352d67fb-1d11-42bd-acee-008d720cae32"
RESOURCE_SP_ID = "a7035858-8f8e-4f5c-a484-3efac710d2ce"
AGENT_API_CLIENT_ID = "af957741-0342-498b-8496-e93706af9e99"
PROVIDER_PRINCIPAL_ID = "96d232e3-2b2e-416b-91fb-066565efac93"
PROVIDER_CLIENT_ID = "8cb2ecb5-3554-4405-be6f-5eda985c2f04"
PUBLIC_PRINCIPAL_ID = "b35e8945-c8ee-4e5b-af55-9e1176d7acaf"
PUBLIC_CLIENT_ID = "5074cac1-5555-43e9-a469-3e0b7e16608e"
SCRIBE_ROLE_ID = "90151a73-578b-4a7d-aa9c-58372dfc2646"
AGENT_ROLE_ID = "5f7c87d1-b2c1-4e92-9d51-54c3160fdfe6"
UNRELATED_ROLE_ID = "00000000-0000-4000-8000-000000000003"

FAKE_AZ = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

root = Path(os.environ["FAKE_AZ_STATE"])
state_path = root / "state.json"
state = json.loads(state_path.read_text())
args = sys.argv[1:]

def option(name):
    return args[args.index(name) + 1]

def save():
    state_path.write_text(json.dumps(state, sort_keys=True))

def mutate(name):
    with (root / "mutations.log").open("a") as output:
        output.write(name + "\n")

if args[:2] == ["account", "show"]:
    result = state["account"]
elif args[:3] == ["ad", "sp", "show"]:
    object_id = option("--id")
    if object_id == state["resource_sp"]["id"]:
        result = {**state["resource_sp"], "appRoles": state["app"]["appRoles"]}
    elif object_id == state["provider_sp"]["id"]:
        result = state["provider_sp"]
    elif object_id == state["public_sp"]["id"]:
        result = state["public_sp"]
    else:
        raise SystemExit(3)
elif args[0] == "rest":
    method = option("--method")
    uri = option("--uri")
    if method == "GET" and "/applications/" in uri:
        result = state["app"]
    elif method == "GET" and state["provider_sp"]["id"] in uri:
        result = {"value": state["provider_assignments"]}
    elif method == "GET" and state["public_sp"]["id"] in uri:
        result = {"value": state["public_assignments"]}
    elif method == "PATCH" and "/applications/" in uri:
        if f'If-Match={state["app"]["@odata.etag"]}' not in args:
            raise SystemExit(4)
        body_argument = option("--body")
        body = json.loads(Path(body_argument.removeprefix("@")).read_text())
        state["app"]["appRoles"] = body["appRoles"]
        state["app"]["@odata.etag"] = 'W/"next"'
        mutate("PATCH")
        save()
        result = {}
    elif method == "POST" and state["provider_sp"]["id"] in uri:
        body = json.loads(option("--body"))
        state["provider_assignments"].append({"id": "assignment-agent", **body})
        mutate("POST")
        save()
        result = {}
    elif method == "DELETE" and state["provider_sp"]["id"] in uri:
        assignment_id = uri.rsplit("/", 1)[-1]
        state["provider_assignments"] = [
            value for value in state["provider_assignments"]
            if value["id"] != assignment_id
        ]
        mutate("DELETE")
        save()
        result = {}
    else:
        raise SystemExit(5)
else:
    raise SystemExit(6)

if "--output" not in args or option("--output") != "none":
    print(json.dumps(result))
'''


def _state() -> dict[str, object]:
    scribe_role = {
        "allowedMemberTypes": ["Application"],
        "description": "Existing Scribe role",
        "displayName": "Scribe transcript write",
        "id": SCRIBE_ROLE_ID,
        "isEnabled": True,
        "value": "Scribe.Transcript.Write",
    }
    unrelated_role = {
        "allowedMemberTypes": ["Application"],
        "description": "Unrelated role that reconciliation must preserve exactly",
        "displayName": "Unrelated role",
        "id": UNRELATED_ROLE_ID,
        "isEnabled": False,
        "value": "Unrelated.Access",
    }
    return {
        "account": {"tenantId": TENANT_ID, "id": SUBSCRIPTION_ID},
        "app": {
            "id": APP_OBJECT_ID,
            "appId": AGENT_API_CLIENT_ID,
            "@odata.etag": 'W/"initial"',
            "appRoles": [scribe_role, unrelated_role],
        },
        "resource_sp": {"id": RESOURCE_SP_ID, "appId": AGENT_API_CLIENT_ID},
        "provider_sp": {"id": PROVIDER_PRINCIPAL_ID, "appId": PROVIDER_CLIENT_ID},
        "public_sp": {"id": PUBLIC_PRINCIPAL_ID, "appId": PUBLIC_CLIENT_ID},
        "provider_assignments": [
            {
                "id": "assignment-scribe",
                "principalId": PROVIDER_PRINCIPAL_ID,
                "resourceId": RESOURCE_SP_ID,
                "appRoleId": SCRIBE_ROLE_ID,
            }
        ],
        "public_assignments": [
            {
                "id": "assignment-public-other",
                "principalId": PUBLIC_PRINCIPAL_ID,
                "resourceId": "00000000-0000-4000-8000-000000000001",
                "appRoleId": "00000000-0000-4000-8000-000000000002",
            }
        ],
    }


def _setup_fake_az(tmp_path: Path) -> tuple[dict[str, str], Path]:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    (state_directory / "state.json").write_text(json.dumps(_state()))
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    fake_az = binary_directory / "az"
    fake_az.write_text(FAKE_AZ)
    fake_az.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{binary_directory}:{os.environ['PATH']}",
        "FAKE_AZ_STATE": str(state_directory),
    }
    return environment, state_directory


def _run(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *arguments],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_default_status_is_read_only_and_verifies_public_exclusion(tmp_path: Path) -> None:
    environment, state_directory = _setup_fake_az(tmp_path)

    result = _run(environment)

    assert result.returncode == 0, result.stderr
    assert "mode=status" in result.stdout
    assert "agent_role=missing" in result.stdout
    assert "provider_staging_assignment=missing" in result.stdout
    assert "public_assignment=excluded" in result.stdout
    assert not (state_directory / "mutations.log").exists()


def test_wrong_tenant_refuses_before_graph_mutation(tmp_path: Path) -> None:
    environment, state_directory = _setup_fake_az(tmp_path)
    state_path = state_directory / "state.json"
    state = json.loads(state_path.read_text())
    state["account"]["tenantId"] = "00000000-0000-4000-8000-000000000000"
    state_path.write_text(json.dumps(state))

    result = _run(environment, "--apply", "--confirm-staging")

    assert result.returncode == 1
    assert "wrong Entra tenant" in result.stderr
    assert not (state_directory / "mutations.log").exists()


def test_wrong_resource_audience_refuses_before_graph_mutation(tmp_path: Path) -> None:
    environment, state_directory = _setup_fake_az(tmp_path)
    state_path = state_directory / "state.json"
    state = json.loads(state_path.read_text())
    state["resource_sp"]["appId"] = "00000000-0000-4000-8000-000000000000"
    state_path.write_text(json.dumps(state))

    result = _run(environment, "--apply", "--confirm-staging")

    assert result.returncode == 1
    assert "resource audience mismatch" in result.stderr
    assert not (state_directory / "mutations.log").exists()


def test_apply_is_idempotent_and_revoke_preserves_unrelated_state(tmp_path: Path) -> None:
    environment, state_directory = _setup_fake_az(tmp_path)
    original = json.loads((state_directory / "state.json").read_text())

    applied = _run(environment, "--apply", "--confirm-staging")
    reapplied = _run(environment, "--apply", "--confirm-staging")

    assert applied.returncode == 0, applied.stderr
    assert reapplied.returncode == 0, reapplied.stderr
    assert "agent_role=ready" in applied.stdout
    assert "provider_staging_assignment=ready" in applied.stdout
    state = json.loads((state_directory / "state.json").read_text())
    assert {role["id"] for role in state["app"]["appRoles"]} == {
        SCRIBE_ROLE_ID,
        AGENT_ROLE_ID,
        UNRELATED_ROLE_ID,
    }
    assert next(
        role for role in state["app"]["appRoles"] if role["id"] == UNRELATED_ROLE_ID
    ) == next(
        role for role in original["app"]["appRoles"] if role["id"] == UNRELATED_ROLE_ID
    )
    assert {value["appRoleId"] for value in state["provider_assignments"]} == {
        SCRIBE_ROLE_ID,
        AGENT_ROLE_ID,
    }
    assert state["public_assignments"] == original["public_assignments"]
    assert (state_directory / "mutations.log").read_text().splitlines() == [
        "PATCH",
        "POST",
    ]

    revoked = _run(environment, "--revoke", "--confirm-staging")
    assert revoked.returncode == 0, revoked.stderr
    state = json.loads((state_directory / "state.json").read_text())
    assert {role["id"] for role in state["app"]["appRoles"]} == {
        SCRIBE_ROLE_ID,
        AGENT_ROLE_ID,
        UNRELATED_ROLE_ID,
    }
    assert [value["appRoleId"] for value in state["provider_assignments"]] == [
        SCRIBE_ROLE_ID
    ]
    assert state["public_assignments"] == original["public_assignments"]
    assert (state_directory / "mutations.log").read_text().splitlines() == [
        "PATCH",
        "POST",
        "DELETE",
    ]
