"""Pure synthetic plan/apply and CLI boundary checks; no live CLI or app import."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

SCRIPT = Path(__file__).parents[2] / "infra/scripts/production-agent-api-role.py"
spec = importlib.util.spec_from_file_location("production_agent_role_plan", SCRIPT)
assert spec and spec.loader
role_plan = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = role_plan
spec.loader.exec_module(role_plan)

CLIENT = "00000000-0000-4000-8000-000000000101"
PRINCIPAL = "00000000-0000-4000-8000-000000000102"
FOUNDER = "00000000-0000-4000-8000-000000000103"
PUBLIC_CLIENT = "00000000-0000-4000-8000-000000000104"
PUBLIC_PRINCIPAL = "00000000-0000-4000-8000-000000000105"
OTHER_ROLE = "00000000-0000-4000-8000-000000000106"
OTHER_RESOURCE = "00000000-0000-4000-8000-000000000107"
SOURCE = {"source_head": "a" * 40, "source_tree": "b" * 40, "founder": FOUNDER}
TARGETS = role_plan.Targets(CLIENT, PRINCIPAL, FOUNDER)
NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def fixed_wall_clock(monkeypatch):
    monkeypatch.setattr(role_plan.time, "time", lambda: NOW + 1)


def assignment(
    identifier: str, principal: str, resource: str, role: str
) -> dict[str, Any]:
    return {
        "id": identifier,
        "principalId": principal,
        "resourceId": resource,
        "appRoleId": role,
        "principalType": "ServicePrincipal",
    }


def fixture(ready: bool = False, assigned: bool = False) -> dict[str, Any]:
    unrelated = {
        "id": OTHER_ROLE,
        "value": "Scribe.Transcript.Write",
        "isEnabled": True,
        "allowedMemberTypes": ["Application"],
        "description": "Synthetic existing Scribe role",
        "displayName": "Scribe capture",
        "origin": "Application",
    }
    roles = [unrelated]
    if ready:
        roles.append({**copy.deepcopy(role_plan.ROLE), "origin": "Application"})
    provider = [assignment("provider-other", PRINCIPAL, OTHER_RESOURCE, OTHER_ROLE)]
    grants = [
        assignment(
            "staging-agent",
            "96d232e3-2b2e-416b-91fb-066565efac93",
            role_plan.RESOURCE,
            role_plan.ROLE_ID,
        )
    ]
    if assigned:
        selected = assignment(
            "provider-agent", PRINCIPAL, role_plan.RESOURCE, role_plan.ROLE_ID
        )
        provider.append(selected)
        grants.append(copy.deepcopy(selected))
    return {
        "provider": {
            "id": role_plan.PROVIDER_UAI,
            "tenantId": role_plan.TENANT,
            "clientId": CLIENT,
            "principalId": PRINCIPAL,
        },
        "public": {
            "id": role_plan.PUBLIC_UAI,
            "tenantId": role_plan.TENANT,
            "clientId": PUBLIC_CLIENT,
            "principalId": PUBLIC_PRINCIPAL,
        },
        "app": {
            "id": role_plan.APPLICATION,
            "appId": role_plan.AUDIENCE,
            "appRoles": roles,
            "@odata.etag": 'W/"initial"',
        },
        "resource": {
            "id": role_plan.RESOURCE,
            "appId": role_plan.AUDIENCE,
            "servicePrincipalType": "Application",
            "appRoles": copy.deepcopy(roles),
        },
        "provider_assignments": provider,
        "public_assignments": [
            assignment("public-other", PUBLIC_PRINCIPAL, OTHER_RESOURCE, OTHER_ROLE)
        ],
        "grants": grants,
    }


class FakeClient(role_plan.Client):
    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state or fixture()
        self.writes: list[tuple[str, str, Any, Any]] = []
        self.source_calls = 0
        self.source_drift_at: int | None = None
        self.before_read: Callable[[FakeClient, str], None] | None = None
        self.lag = 0

    def source(self, targets: Any) -> dict[str, Any]:
        targets.validate()
        self.source_calls += 1
        if self.source_drift_at == self.source_calls:
            return {**SOURCE, "source_head": "c" * 40}
        return copy.deepcopy(SOURCE)

    def json(self, args: list[str]) -> Any:
        assert args[:3] == ["az", "identity", "show"]
        key = "provider" if role_plan.PROVIDER_UAI in args else "public"
        return copy.deepcopy(self.state[key])

    def graph(
        self, path: str, method: str = "GET", body: Any = None, etag: str | None = None
    ) -> Any:
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(body), etag))
            if method == "PATCH":
                assert path == f"applications/{role_plan.APPLICATION}"
                assert set(body) == {"appRoles"}
                assert all("origin" not in value for value in body["appRoles"])
                assert etag == self.state["app"].get("@odata.etag")
                self.state["app"]["appRoles"] = [
                    {**role, "origin": "Application"} for role in body["appRoles"]
                ]
                self.state["app"]["@odata.etag"] = 'W/"next"'
                if not self.lag:
                    self.state["resource"]["appRoles"] = copy.deepcopy(
                        self.state["app"]["appRoles"]
                    )
            elif method == "POST":
                assert path == f"servicePrincipals/{PRINCIPAL}/appRoleAssignments"
                assert body == {
                    "principalId": PRINCIPAL,
                    "resourceId": role_plan.RESOURCE,
                    "appRoleId": role_plan.ROLE_ID,
                }
                selected = {
                    "id": "new-provider-agent",
                    "principalType": "ServicePrincipal",
                    **body,
                }
                self.state["provider_assignments"].append(selected)
                self.state["grants"].append(copy.deepcopy(selected))
            else:
                raise AssertionError("unexpected write")
            return None
        if self.before_read:
            self.before_read(self, path)
        if path == f"applications/{role_plan.APPLICATION}":
            return copy.deepcopy(self.state["app"])
        if path == f"servicePrincipals/{role_plan.RESOURCE}":
            if self.lag and self.writes:
                self.lag -= 1
                if not self.lag:
                    self.state["resource"]["appRoles"] = copy.deepcopy(
                        self.state["app"]["appRoles"]
                    )
            return copy.deepcopy(self.state["resource"])
        for key in ("provider", "public"):
            identity = self.state[key]
            if path == f"servicePrincipals/{identity['principalId']}":
                return {
                    "id": identity["principalId"],
                    "appId": identity["clientId"],
                    "servicePrincipalType": "ManagedIdentity",
                }
            if (
                path
                == f"servicePrincipals/{identity['principalId']}/appRoleAssignments"
            ):
                return {"value": copy.deepcopy(self.state[key + "_assignments"])}
        if path == f"servicePrincipals/{role_plan.RESOURCE}/appRoleAssignedTo":
            return {"value": copy.deepcopy(self.state["grants"])}
        raise AssertionError(path)


def prepared(client: FakeClient) -> dict[str, Any]:
    result = role_plan.prepare(client, TARGETS, NOW)
    assert not client.writes
    return result


def test_missing_role_and_assignment_are_explicit_then_narrowly_applied() -> None:
    client = FakeClient()
    before = copy.deepcopy(client.state)
    plan = prepared(client)
    assert plan["actions"] == [
        "add_fixed_application_role",
        "wait_for_fixed_role_projection",
        "assign_fixed_role_to_production_provider",
    ]
    result = role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["PATCH", "POST"]
    assert client.state["app"]["appRoles"][0] == before["app"]["appRoles"][0]
    assert client.state["public_assignments"] == before["public_assignments"]
    assert client.state["provider_assignments"][0] == before["provider_assignments"][0]
    assert client.state["grants"][0] == before["grants"][0]
    assert result["assignment_count"] == 1 and result["session_prep_activated"] is False


def test_existing_role_requires_only_exact_assignment() -> None:
    client = FakeClient(fixture(ready=True))
    plan = prepared(client)
    assert plan["actions"] == ["assign_fixed_role_to_production_provider"]
    role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["POST"]


def test_existing_assignment_is_idempotent_and_performs_no_write() -> None:
    client = FakeClient(fixture(ready=True, assigned=True))
    plan = prepared(client)
    assert plan["actions"] == []
    role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert not client.writes


def test_missing_projection_is_planned_and_bounded(monkeypatch) -> None:
    client = FakeClient(fixture(ready=True))
    client.state["resource"]["appRoles"] = client.state["resource"]["appRoles"][:1]
    plan = prepared(client)
    assert plan["actions"][0] == "wait_for_fixed_role_projection"
    sleeps = []
    monkeypatch.setattr(role_plan.time, "sleep", sleeps.append)
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert sleeps == [2] * 5 and not client.writes


@pytest.mark.parametrize(
    "key,value",
    [
        ("audience", "00000000-0000-4000-8000-000000000199"),
        ("provider_client_id", "8cb2ecb5-3554-4405-be6f-5eda985c2f04"),
        ("provider_principal_id", "96d232e3-2b2e-416b-91fb-066565efac93"),
        ("provider_principal_id", CLIENT),
        ("founder_object_id", PRINCIPAL),
        ("provider_client_id", "00000000-0000-4000-8000-0000000001A1"),
        ("provider_client_id", "00000000-0000-0000-0000-000000000000"),
        ("provider_principal_id", "../unrelated"),
    ],
)
def test_targets_cannot_redirect_to_staging_or_another_audience(key, value) -> None:
    values = vars(TARGETS).copy()
    values[key] = value
    with pytest.raises((role_plan.Refused, ValueError)):
        role_plan.Targets(**values).validate()


@pytest.mark.parametrize(
    "change",
    [
        lambda state: state["provider"].update(clientId=PUBLIC_CLIENT),
        lambda state: state["provider"].update(principalId=PUBLIC_PRINCIPAL),
        lambda state: state["public"].update(clientId=CLIENT),
        lambda state: state["app"].update(appId=OTHER_RESOURCE),
        lambda state: state["resource"].update(appId=OTHER_RESOURCE),
        lambda state: state["resource"].update(servicePrincipalType="ManagedIdentity"),
        lambda state: state["provider"].update(tenantId=OTHER_RESOURCE),
        lambda state: state["provider"].update(
            id=role_plan.PROVIDER_UAI.replace("prod", "staging")
        ),
    ],
)
def test_wrong_actual_identity_or_resource_refuses_before_write(change) -> None:
    client = FakeClient()
    change(client.state)
    with pytest.raises((role_plan.Refused, AssertionError)):
        prepared(client)
    assert not client.writes


@pytest.mark.parametrize(
    "change",
    [
        lambda role: role.update(allowedMemberTypes=["User"]),
        lambda role: role.update(allowedMemberTypes=["Application", "User"]),
        lambda role: role.update(isEnabled=False),
        lambda role: role.update(displayName="Unreviewed authority"),
        lambda role: role.update(id=OTHER_RESOURCE),
        lambda role: role.update(origin="ServicePrincipal"),
        lambda role: role.update(extra="unreviewed"),
    ],
)
def test_conflicting_or_broadened_existing_role_is_not_repaired(change) -> None:
    client = FakeClient(fixture(ready=True))
    change(client.state["app"]["appRoles"][-1])
    with pytest.raises(role_plan.Refused):
        prepared(client)
    assert not client.writes


def test_duplicate_role_identity_refuses() -> None:
    client = FakeClient(fixture(ready=True))
    duplicate = copy.deepcopy(client.state["app"]["appRoles"][-1])
    duplicate["id"] = OTHER_RESOURCE
    client.state["app"]["appRoles"].append(duplicate)
    with pytest.raises(role_plan.Refused):
        prepared(client)


@pytest.mark.parametrize("collection", ["provider_assignments", "grants"])
def test_duplicate_target_assignment_refuses(collection) -> None:
    client = FakeClient(fixture(ready=True, assigned=True))
    duplicate = copy.deepcopy(client.state[collection][-1])
    duplicate["id"] = "duplicate-agent"
    client.state[collection].append(duplicate)
    with pytest.raises(role_plan.Refused):
        prepared(client)
    assert not client.writes


def test_public_agent_assignment_refuses_and_is_not_removed() -> None:
    client = FakeClient(fixture(ready=True))
    selected = assignment(
        "bad-public-agent", PUBLIC_PRINCIPAL, role_plan.RESOURCE, role_plan.ROLE_ID
    )
    client.state["public_assignments"].append(selected)
    client.state["grants"].append(copy.deepcopy(selected))
    with pytest.raises(role_plan.Refused):
        prepared(client)
    assert not client.writes


@pytest.mark.parametrize(
    "change",
    [
        lambda state: state["provider_assignments"][0].update(
            principalId=PUBLIC_PRINCIPAL
        ),
        lambda state: state["provider_assignments"][0].update(principalType="User"),
        lambda state: state["grants"][0].update(principalType="User"),
        lambda state: state["grants"][0].update(resourceId=OTHER_RESOURCE),
    ],
)
def test_assignment_authority_conflicts_are_refused(change) -> None:
    client = FakeClient()
    change(client.state)
    with pytest.raises(role_plan.Refused):
        prepared(client)
    assert not client.writes


def test_incomplete_collection_is_not_treated_as_empty() -> None:
    with pytest.raises(role_plan.Refused):
        role_plan.assignments(
            {"value": [], "@odata.nextLink": role_plan.GRAPH + "/next"}
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan.update(actions=[]),
        lambda plan: plan.update(before_digest="0" * 64),
        lambda plan: plan.update(scope="staging"),
        lambda plan: plan.update(version=True),
        lambda plan: plan.update(expires_at=NOW + 7200),
        lambda plan: plan["targets"].update(provider_principal_id=PUBLIC_PRINCIPAL),
        lambda plan: plan["source"].update(source_head="c" * 40),
        lambda plan: plan.update(
            fixed_role={**role_plan.ROLE, "allowedMemberTypes": ["User"]}
        ),
    ],
)
def test_rehashed_forged_plan_is_not_authority(mutation) -> None:
    client = FakeClient()
    plan = copy.deepcopy(prepared(client))
    mutation(plan)
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert not client.writes


@pytest.mark.parametrize(
    "now,confirmed",
    [
        (NOW - 1, True),
        (NOW + role_plan.PLAN_LIFETIME, True),
        (NOW + 1, False),
        (NOW + 1, 1),
    ],
)
def test_expired_future_or_unconfirmed_apply_is_default_off(now, confirmed) -> None:
    client = FakeClient()
    plan = prepared(client)
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, now, confirmed)
    assert not client.writes


def test_concurrent_role_change_refuses_before_any_write() -> None:
    client = FakeClient()
    plan = prepared(client)
    client.state["app"]["appRoles"][0]["description"] = "Concurrent unrelated change"
    client.state["resource"]["appRoles"] = copy.deepcopy(
        client.state["app"]["appRoles"]
    )
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert not client.writes


def test_source_drift_after_role_creation_prevents_assignment() -> None:
    client = FakeClient()
    plan = prepared(client)
    client.source_drift_at = client.source_calls + 3
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["PATCH"]
    assert not role_plan.matching_assignments(client.state["provider_assignments"])


def test_post_write_unrelated_change_is_refused_without_automatic_rollback() -> None:
    client = FakeClient(fixture(ready=True))
    plan = prepared(client)

    def drift(fake, path):
        if fake.writes:
            fake.state["public_assignments"][0]["appRoleId"] = role_plan.ROLE_ID
            fake.before_read = None

    client.before_read = drift
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["POST"]


@pytest.mark.parametrize("ready", [False, True])
def test_admission_reads_cannot_outlive_plan_before_first_write(
    monkeypatch, ready
) -> None:
    client = FakeClient(fixture(ready=ready))
    plan = prepared(client)
    wall = [plan["expires_at"] - 1]
    monkeypatch.setattr(role_plan.time, "time", lambda: wall[0])

    def advance(fake, path):
        wall[0] = plan["expires_at"]

    client.before_read = advance
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, plan["expires_at"] - 1, True)
    assert not client.writes


def test_expiry_after_role_patch_prevents_assignment_post(monkeypatch) -> None:
    client = FakeClient()
    plan = prepared(client)
    wall = [plan["expires_at"] - 1]
    monkeypatch.setattr(role_plan.time, "time", lambda: wall[0])

    def advance(fake, path):
        if fake.writes:
            wall[0] = plan["expires_at"]

    client.before_read = advance
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, plan["expires_at"] - 1, True)
    assert [write[0] for write in client.writes] == ["PATCH"]


def test_admitted_post_may_finish_read_only_after_expiry(monkeypatch) -> None:
    client = FakeClient(fixture(ready=True))
    plan = prepared(client)
    wall = [plan["expires_at"] - 1]
    monkeypatch.setattr(role_plan.time, "time", lambda: wall[0])

    def advance(fake, path):
        if fake.writes:
            wall[0] = plan["expires_at"] + 1

    client.before_read = advance
    result = role_plan.apply(client, TARGETS, plan, plan["expires_at"] - 1, True)
    assert [write[0] for write in client.writes] == ["POST"]
    assert result["assignment_count"] == 1 and wall[0] > plan["expires_at"]


class LaggingAssignmentClient(FakeClient):
    """One successful POST, followed by independently lagging read projections."""

    def __init__(self, views: set[str], observations: int = 2) -> None:
        super().__init__(fixture(ready=True))
        self.views = views
        self.hidden_observations = observations
        self.observations = 0

    def graph(self, path, method="GET", body=None, etag=None) -> Any:
        result = super().graph(path, method, body, etag)
        posted = any(write[0] == "POST" for write in self.writes)
        if method != "GET" or not posted:
            return result
        if path == f"servicePrincipals/{role_plan.RESOURCE}":
            self.observations += 1
        collection = {
            f"servicePrincipals/{PRINCIPAL}/appRoleAssignments": "provider",
            f"servicePrincipals/{role_plan.RESOURCE}/appRoleAssignedTo": "resource",
        }.get(path)
        if collection in self.views and self.observations <= self.hidden_observations:
            result["value"] = [
                item
                for item in result["value"]
                if item not in role_plan.matching_assignments(result["value"], PRINCIPAL)
            ]
        return result


@pytest.mark.parametrize("views", [{"provider"}, {"resource"}, {"provider", "resource"}])
def test_assignment_projection_lag_reconciles_with_reads_and_one_post(
    monkeypatch, views
) -> None:
    client = LaggingAssignmentClient(views)
    before = copy.deepcopy(client.state)
    plan = prepared(client)
    sleeps = []
    monkeypatch.setattr(role_plan.time, "sleep", sleeps.append)
    result = role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["POST"]
    assert sleeps == [2, 2] and client.observations == 3
    assert result["assignment_count"] == 1
    assert client.state["provider_assignments"][0] == before["provider_assignments"][0]
    assert client.state["public_assignments"] == before["public_assignments"]
    assert client.state["grants"][0] == before["grants"][0]


def test_assignment_projection_wait_exhaustion_never_repeats_post(monkeypatch) -> None:
    client = LaggingAssignmentClient({"resource"}, observations=100)
    plan = prepared(client)
    sleeps = []
    monkeypatch.setattr(role_plan.time, "sleep", sleeps.append)
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["POST"]
    assert sleeps == [2] * 5 and client.observations == 6


def test_assignment_read_budget_timeout_does_not_repeat_post(monkeypatch) -> None:
    client = LaggingAssignmentClient({"resource"}, observations=100)
    plan = prepared(client)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: pytest.fail("live CLI reached")
    )

    def exhaust_budget(fake, path):
        if fake.writes:
            fake.deadline = role_plan.time.monotonic() - 1
            role_plan.Client.command(fake, ["az", "account", "show"])

    client.before_read = exhaust_budget
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["POST"]


def test_missing_unrelated_grant_is_not_hidden_by_target_view_lag(monkeypatch) -> None:
    client = LaggingAssignmentClient({"resource"})
    plan = prepared(client)
    sleeps = []
    monkeypatch.setattr(role_plan.time, "sleep", sleeps.append)

    def drift(fake, path):
        if fake.writes:
            fake.state["grants"].pop(0)
            fake.before_read = None

    client.before_read = drift
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["POST"] and not sleeps


@pytest.mark.parametrize(
    "change",
    [
        lambda state: state["grants"][-1].update(id="conflicting-visible-target"),
        lambda state: state["grants"].append(
            {**state["grants"][-1], "id": "duplicate-visible-target"}
        ),
        lambda state: state["provider_assignments"].pop(0),
        lambda state: state["grants"].pop(0),
        lambda state: state["public_assignments"][0].update(appRoleId=role_plan.ROLE_ID),
        lambda state: state["provider"].update(clientId=OTHER_RESOURCE),
    ],
)
def test_replication_wait_does_not_retry_conflicts_or_unrelated_drift(
    monkeypatch, change
) -> None:
    client = LaggingAssignmentClient(set())
    plan = prepared(client)
    sleeps = []
    monkeypatch.setattr(role_plan.time, "sleep", sleeps.append)

    def drift(fake, path):
        if fake.writes:
            change(fake.state)
            fake.before_read = None

    client.before_read = drift
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["POST"]
    assert not sleeps


def test_existing_assignment_inconsistent_views_are_not_replication_allowance() -> None:
    client = FakeClient(fixture(ready=True, assigned=True))
    client.state["grants"].pop()
    with pytest.raises(role_plan.Refused):
        prepared(client)
    assert not client.writes


def test_observed_new_target_id_cannot_change_while_other_view_lags(monkeypatch) -> None:
    client = LaggingAssignmentClient({"resource"}, observations=100)
    plan = prepared(client)
    sleeps = []
    monkeypatch.setattr(role_plan.time, "sleep", sleeps.append)

    def drift(fake, path):
        if path == f"servicePrincipals/{role_plan.RESOURCE}" and fake.observations >= 1:
            fake.state["provider_assignments"][-1]["id"] = "replaced-target"
            fake.state["grants"][-1]["id"] = "replaced-target"
            fake.before_read = None

    client.before_read = drift
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert [write[0] for write in client.writes] == ["POST"] and sleeps == [2]


def test_preexisting_target_assignment_id_remains_immutable() -> None:
    client = FakeClient(fixture(ready=True, assigned=True))
    plan = prepared(client)
    observations = [0]

    def drift(fake, path):
        if path == f"applications/{role_plan.APPLICATION}":
            observations[0] += 1
            if observations[0] == 5:
                fake.state["provider_assignments"][-1]["id"] = "replaced-existing"
                fake.state["grants"][-1]["id"] = "replaced-existing"

    client.before_read = drift
    with pytest.raises(role_plan.Refused):
        role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert not client.writes and observations[0] == 5


def test_default_unrelated_assignment_ids_remain_preserved_without_authority() -> None:
    client = FakeClient(fixture(ready=True))
    zero = "00000000-0000-0000-0000-000000000000"
    client.state["provider_assignments"][0]["appRoleId"] = zero
    client.state["public_assignments"][0]["appRoleId"] = zero
    client.state["grants"].append(
        assignment("default-existing", PUBLIC_PRINCIPAL, role_plan.RESOURCE, zero)
    )
    before = copy.deepcopy(client.state)
    plan = prepared(client)
    result = role_plan.apply(client, TARGETS, plan, NOW + 1, True)
    assert result["assignment_count"] == 1
    assert [write[0] for write in client.writes] == ["POST"]
    assert client.writes[0][2]["appRoleId"] == role_plan.ROLE_ID != zero
    assert client.state["provider_assignments"][0] == before["provider_assignments"][0]
    assert client.state["public_assignments"] == before["public_assignments"]
    assert client.state["grants"][:-1] == before["grants"]


@pytest.mark.parametrize("key", ["principalId", "resourceId"])
def test_default_role_metadata_cannot_weaken_assignment_identity(key) -> None:
    item = assignment("invalid-identity", PRINCIPAL, OTHER_RESOURCE, OTHER_ROLE)
    item[key] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(role_plan.Refused):
        role_plan.assignments({"value": [item]})


def test_real_graph_builder_uses_valid_fixed_api_shapes_and_no_secret_files(
    monkeypatch,
) -> None:
    client = role_plan.Client(TARGETS)
    calls = []
    monkeypatch.setattr(client, "json", lambda args: calls.append(args) or {})
    monkeypatch.setattr(client, "command", lambda args: calls.append(args) or b"")
    client.graph(f"applications/{role_plan.APPLICATION}")
    client.graph(f"servicePrincipals/{PRINCIPAL}")
    client.graph(
        f"applications/{role_plan.APPLICATION}",
        "PATCH",
        {"appRoles": [role_plan.ROLE]},
        'W/"v1"',
    )
    app_url = calls[0][calls[0].index("--uri") + 1]
    assert (
        app_url
        == f"{role_plan.GRAPH}/applications/{role_plan.APPLICATION}?$select=id,appId,appRoles"
    )
    assert "servicePrincipalType" not in app_url
    assert "servicePrincipalType" in calls[1][calls[1].index("--uri") + 1]
    assert 'If-Match=W/"v1"' in calls[2]
    assert calls[2].count("--headers") == 1
    assert not any("@" == value[:1] for value in calls[2])
    with pytest.raises(role_plan.Refused):
        client.graph("https://unrelated.invalid", "GET")
    with pytest.raises(role_plan.Refused):
        client.graph(f"applications/{role_plan.APPLICATION}", "DELETE")


@pytest.mark.parametrize(
    "principal,resource,role",
    [
        (PUBLIC_PRINCIPAL, role_plan.RESOURCE, role_plan.ROLE_ID),
        (PRINCIPAL, OTHER_RESOURCE, role_plan.ROLE_ID),
        (PRINCIPAL, role_plan.RESOURCE, OTHER_ROLE),
    ],
)
def test_real_write_builder_refuses_an_unrelated_principal_resource_or_role(
    monkeypatch, principal, resource, role
) -> None:
    client = role_plan.Client(TARGETS)
    monkeypatch.setattr(
        client, "command", lambda *_: pytest.fail("unrelated write reached CLI")
    )
    with pytest.raises(role_plan.Refused):
        client.graph(
            f"servicePrincipals/{principal}/appRoleAssignments",
            "POST",
            {"principalId": principal, "resourceId": resource, "appRoleId": role},
        )


@pytest.mark.parametrize(
    "change",
    [
        "founder",
        "azure_user",
        "tenant",
        "subscription",
        "source",
        "dirty",
        "code",
        "valid",
    ],
)
def test_real_source_guard_requires_actual_founder_normal_account_and_current_master(
    monkeypatch, change
) -> None:
    client = role_plan.Client()

    def command(args):
        if "status" in args:
            return b"?? changed.py" if change == "dirty" else b""
        if "show" in args:
            return b"unreviewed code" if change == "code" else SCRIPT.read_bytes()
        return (("b" if "HEAD^{tree}" in args else "a") * 40 + "\n").encode()

    def read(args):
        if args[:3] == ["gh", "api", "--hostname"]:
            if "user" in args:
                return {
                    "login": "Unrelated" if change == "founder" else "JohnClem",
                    "type": "User",
                }
            if any("git/ref" in value for value in args):
                return {"object": {"sha": ("c" if change == "source" else "a") * 40}}
            return {"tree": {"sha": "b" * 40}}
        if args[:3] == ["az", "account", "show"]:
            return {
                "tenantId": OTHER_RESOURCE if change == "tenant" else role_plan.TENANT,
                "id": OTHER_RESOURCE
                if change == "subscription"
                else role_plan.SUBSCRIPTION,
                "userType": "servicePrincipal" if change == "azure_user" else "user",
            }
        return FOUNDER

    monkeypatch.setattr(client, "command", command)
    monkeypatch.setattr(client, "json", read)
    if change == "valid":
        assert client.source(TARGETS) == SOURCE
    else:
        with pytest.raises(role_plan.Refused):
            client.source(TARGETS)


def test_plan_file_hash_ownership_and_exclusive_output(tmp_path) -> None:
    value = prepared(FakeClient())
    path = tmp_path / "plan.json"
    role_plan.save(path, value)
    assert (
        role_plan.load_plan(path, hashlib.sha256(path.read_bytes()).hexdigest())
        == value
    )
    with pytest.raises(FileExistsError):
        role_plan.save(path, value)
    with pytest.raises(role_plan.Refused):
        role_plan.load_plan(path, "0" * 64)
    path.chmod(0o666)
    with pytest.raises(role_plan.Refused):
        role_plan.load_plan(path, hashlib.sha256(path.read_bytes()).hexdigest())
    link = tmp_path / "symlink.json"
    link.symlink_to(path)
    with pytest.raises(role_plan.Refused):
        role_plan.load_plan(link, "0" * 64)
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    with pytest.raises(role_plan.Refused):
        role_plan.load_plan(fifo, "0" * 64)


def test_cli_default_plan_and_apply_confirmation_fail_before_client(
    monkeypatch, tmp_path, capsys
) -> None:
    argv = [
        str(SCRIPT),
        "apply",
        "--provider-client-id",
        CLIENT,
        "--provider-principal-id",
        PRINCIPAL,
        "--founder-object-id",
        FOUNDER,
        "--agent-api-audience",
        role_plan.AUDIENCE,
        "--output",
        str(tmp_path / "result.json"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(
        role_plan.Client,
        "command",
        lambda *_: pytest.fail("CLI operation before exact apply admission"),
    )
    assert role_plan.main() == 2
    assert capsys.readouterr().err == "production_agent_role_refused\n"
    assert not (tmp_path / "result.json").exists()


def test_normal_command_bounds_timeout_and_never_prints_cli_stderr(
    monkeypatch, capsys
) -> None:
    client = role_plan.Client()

    def refused(args, **kwargs):
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert 0 < kwargs["timeout"] <= 20
        return subprocess.CompletedProcess(args, 2)

    monkeypatch.setattr(subprocess, "run", refused)
    with pytest.raises(role_plan.Refused):
        client.command(["az", "account", "show"])
    assert capsys.readouterr().out == "" and capsys.readouterr().err == ""


def test_staging_reconciler_source_is_not_extended_to_production() -> None:
    original = SCRIPT.with_name("reconcile-agent-api-role.sh").read_text()
    assert "--confirm-staging" in original and "provider staging UAI" in original
    assert "production-agent-api-role" not in original
