from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "production_agent_api_identity_plan.py"
SPEC = importlib.util.spec_from_file_location(
    "production_agent_api_identity_plan", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
m: ModuleType = MODULE

NOW = datetime(2026, 10, 29, 16, 0, tzinfo=UTC)
BEFORE_AT = NOW - timedelta(minutes=5)
AFTER_AT = NOW - timedelta(minutes=1)
CANDIDATE_SHA = "a" * 40


def _uuid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012x}"


TENANT_ID = _uuid(1)
APPLICATION_OBJECT_ID = _uuid(2)
RESOURCE_SERVICE_PRINCIPAL_ID = _uuid(3)
AUDIENCE_CLIENT_ID = _uuid(4)
AGENT_ROLE_ID = _uuid(5)
SCRIBE_ROLE_ID = _uuid(6)
PROVIDER_CLIENT_ID = _uuid(7)
PROVIDER_PRINCIPAL_ID = _uuid(8)
PUBLIC_CLIENT_ID = _uuid(9)
PUBLIC_PRINCIPAL_ID = _uuid(10)
UNRELATED_ROLE_ID = _uuid(11)
PROVIDER_IDENTITY_NAME = "cosmaneura-sovereign-prod-provider-runtime"
PUBLIC_IDENTITY_NAME = "cosmaneura-sovereign-prod-public-runtime"


def _time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _identity(name: str, client_id: str, principal_id: str) -> dict[str, str]:
    return {
        "name": name,
        "resource_id": (
            "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13"
            "/resourceGroups/cosmaneura-sovereign-prod"
            "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
            f"{name}"
        ),
        "client_id": client_id,
        "principal_id": principal_id,
    }


def _role(
    role_id: str,
    value: str,
    display_name: str,
    description: str,
) -> dict[str, Any]:
    return {
        "id": role_id,
        "value": value,
        "display_name": display_name,
        "description": description,
        "is_enabled": True,
        "allowed_member_types": ["Application"],
    }


def _roles() -> list[dict[str, Any]]:
    return [
        _role(
            AGENT_ROLE_ID,
            "AgentApi.Access",
            "Agent API access",
            "Allows the provider Sovereign workload to call the governed Agent API.",
        ),
        _role(
            SCRIBE_ROLE_ID,
            "Scribe.Transcript.Write",
            "Scribe transcript write",
            "Allows the Scribe workload to submit a governed transcript.",
        ),
        _role(
            UNRELATED_ROLE_ID,
            "Existing.Unrelated",
            "Existing unrelated role",
            "An existing role that the plan must preserve.",
        ),
    ]


def _assignment(
    assignment_id: str,
    principal_id: str,
    resource_id: str,
    role_id: str,
) -> dict[str, str]:
    return {
        "id": assignment_id,
        "principal_id": principal_id,
        "resource_id": resource_id,
        "app_role_id": role_id,
    }


SCRIBE_ASSIGNMENT = _assignment(
    _uuid(20), _uuid(21), RESOURCE_SERVICE_PRINCIPAL_ID, SCRIBE_ROLE_ID
)
UNRELATED_INBOUND_ASSIGNMENT = _assignment(
    _uuid(22), _uuid(23), RESOURCE_SERVICE_PRINCIPAL_ID, UNRELATED_ROLE_ID
)
PROVIDER_OTHER_ASSIGNMENT = _assignment(
    _uuid(24), PROVIDER_PRINCIPAL_ID, _uuid(25), _uuid(26)
)
PUBLIC_OTHER_ASSIGNMENT = _assignment(
    _uuid(27), PUBLIC_PRINCIPAL_ID, _uuid(28), _uuid(29)
)
TARGET_ASSIGNMENT = _assignment(
    _uuid(30), PROVIDER_PRINCIPAL_ID, RESOURCE_SERVICE_PRINCIPAL_ID, AGENT_ROLE_ID
)
PUBLIC_TARGET_ASSIGNMENT = _assignment(
    _uuid(31), PUBLIC_PRINCIPAL_ID, RESOURCE_SERVICE_PRINCIPAL_ID, AGENT_ROLE_ID
)


def _snapshot(*, observed_at: datetime, with_target: bool = False) -> dict[str, Any]:
    provider = _identity(
        PROVIDER_IDENTITY_NAME, PROVIDER_CLIENT_ID, PROVIDER_PRINCIPAL_ID
    )
    public = _identity(PUBLIC_IDENTITY_NAME, PUBLIC_CLIENT_ID, PUBLIC_PRINCIPAL_ID)
    roles = _roles()
    inbound = [deepcopy(SCRIBE_ASSIGNMENT), deepcopy(UNRELATED_INBOUND_ASSIGNMENT)]
    provider_outgoing = [deepcopy(PROVIDER_OTHER_ASSIGNMENT)]
    if with_target:
        inbound.append(deepcopy(TARGET_ASSIGNMENT))
        provider_outgoing.append(deepcopy(TARGET_ASSIGNMENT))
    return {
        "observed_at": _time(observed_at),
        "application": {
            "object_id": APPLICATION_OBJECT_ID,
            "app_id": AUDIENCE_CLIENT_ID,
            "app_roles_collection_complete": True,
            "app_roles": deepcopy(roles),
        },
        "resource_service_principal": {
            "object_id": RESOURCE_SERVICE_PRINCIPAL_ID,
            "app_id": AUDIENCE_CLIENT_ID,
            "app_roles_collection_complete": True,
            "app_roles": deepcopy(roles),
        },
        "provider_identity": deepcopy(provider),
        "public_identity": deepcopy(public),
        "provider_service_principal": {
            "object_id": PROVIDER_PRINCIPAL_ID,
            "app_id": PROVIDER_CLIENT_ID,
            "service_principal_type": "ManagedIdentity",
        },
        "public_service_principal": {
            "object_id": PUBLIC_PRINCIPAL_ID,
            "app_id": PUBLIC_CLIENT_ID,
            "service_principal_type": "ManagedIdentity",
        },
        "provider_container_app": {
            "resource_group": "cosmaneura-sovereign-prod",
            "name": "cosmaneura-sovereign-prod-app",
            "user_assigned_identity_resource_ids": [provider["resource_id"]],
        },
        "public_container_app": {
            "resource_group": "cosmaneura-sovereign-prod",
            "name": "cosmaneura-web-prod-app",
            "user_assigned_identity_resource_ids": [public["resource_id"]],
        },
        "agent_api_resource_assignments": {
            "collection_complete": True,
            "items": inbound,
        },
        "provider_outgoing_assignments": {
            "collection_complete": True,
            "items": provider_outgoing,
        },
        "public_outgoing_assignments": {
            "collection_complete": True,
            "items": [deepcopy(PUBLIC_OTHER_ASSIGNMENT)],
        },
    }


def _document(
    *, with_target: bool = False, observed_at: datetime = BEFORE_AT
) -> dict[str, Any]:
    provider = _identity(
        PROVIDER_IDENTITY_NAME, PROVIDER_CLIENT_ID, PROVIDER_PRINCIPAL_ID
    )
    public = _identity(PUBLIC_IDENTITY_NAME, PUBLIC_CLIENT_ID, PUBLIC_PRINCIPAL_ID)
    return {
        "schema_version": 1,
        "environment": "production",
        "mode": "plan",
        "candidate_source_sha": CANDIDATE_SHA,
        "authority": {
            "tenant_id": TENANT_ID,
            "subscription_id": "bab2a709-1fcb-4c87-a9fc-802f7477ec13",
            "core_resource_group": "cosmaneura-prod",
            "core_api_container_app": "cosmaneura-prod-app",
            "sovereign_resource_group": "cosmaneura-sovereign-prod",
            "provider_container_app": "cosmaneura-sovereign-prod-app",
            "public_container_app": "cosmaneura-web-prod-app",
        },
        "bindings": {
            "entra_tenant_id": TENANT_ID,
            "agent_api_application_object_id": APPLICATION_OBJECT_ID,
            "agent_api_resource_service_principal_id": (RESOURCE_SERVICE_PRINCIPAL_ID),
            "agent_api_audience_client_id": AUDIENCE_CLIENT_ID,
            "agent_api_access_role_id": AGENT_ROLE_ID,
            "scribe_transcript_write_role_id": SCRIBE_ROLE_ID,
            "provider_identity": provider,
            "public_identity": public,
        },
        "core_rollout": {
            "agent_api_audience": AUDIENCE_CLIENT_ID,
            "sovereign_client_id": PROVIDER_CLIENT_ID,
        },
        "before": _snapshot(observed_at=observed_at, with_target=with_target),
        "after": None,
        "expected_plan_sha256": None,
    }


def _verification() -> dict[str, Any]:
    document = _document()
    plan = m.evaluate(document, now=NOW)
    document["mode"] = "verify"
    document["expected_plan_sha256"] = plan["plan_sha256"]
    document["after"] = _snapshot(observed_at=AFTER_AT, with_target=True)
    return document


def _rejects(document: dict[str, Any]) -> None:
    with pytest.raises(m.IdentityPlanRefused):
        m.evaluate(document, now=NOW)


def test_plan_requires_only_the_exact_provider_assignment() -> None:
    report = m.evaluate(_document(), now=NOW)

    assert report == {
        "agent_role": "verified",
        "assignment_action": "create_required",
        "environment": "production",
        "evidence_scope": "operator_supplied_read_only_snapshot",
        "evidence_sha256": report["evidence_sha256"],
        "mode": "plan",
        "plan_sha256": report["plan_sha256"],
        "public_assignment": "excluded",
        "schema_version": 1,
        "scribe_grants": "preserved",
        "status": "ready",
    }
    assert re.fullmatch(r"sha256_[0-9a-f]{64}", report["plan_sha256"])


def test_plan_is_no_change_when_exact_assignment_already_exists() -> None:
    report = m.evaluate(_document(with_target=True), now=NOW)

    assert report["assignment_action"] == "no_change"


def test_semantically_identical_collection_order_has_same_digests() -> None:
    first = _document()
    second = deepcopy(first)
    for name in ("application", "resource_service_principal"):
        second["before"][name]["app_roles"].reverse()
    second["before"]["agent_api_resource_assignments"]["items"].reverse()

    first_report = m.evaluate(first, now=NOW)
    second_report = m.evaluate(second, now=NOW)

    assert first_report["plan_sha256"] == second_report["plan_sha256"]
    assert first_report["evidence_sha256"] == second_report["evidence_sha256"]


def test_verification_accepts_only_the_exact_target_delta() -> None:
    report = m.evaluate(_verification(), now=NOW)

    assert report["assignment_action"] == "verified"
    assert report["public_assignment"] == "excluded"
    assert report["scribe_grants"] == "preserved"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["authority"].__setitem__("subscription_id", _uuid(40)),
        lambda value: value["core_rollout"].__setitem__(
            "sovereign_client_id", _uuid(41)
        ),
        lambda value: value["before"]["provider_identity"].__setitem__(
            "client_id", _uuid(42)
        ),
    ],
)
def test_authority_rollout_and_identity_mismatches_fail_closed(
    mutation: Any,
) -> None:
    document = _document()
    mutation(document)

    _rejects(document)


def test_public_agent_assignment_fails_in_both_views() -> None:
    document = _document()
    before = document["before"]
    before["agent_api_resource_assignments"]["items"].append(
        deepcopy(PUBLIC_TARGET_ASSIGNMENT)
    )
    before["public_outgoing_assignments"]["items"].append(
        deepcopy(PUBLIC_TARGET_ASSIGNMENT)
    )

    _rejects(document)


@pytest.mark.parametrize("snapshot_name", ["application", "resource_service_principal"])
def test_missing_or_changed_agent_role_fails_closed(snapshot_name: str) -> None:
    missing = _document()
    missing["before"][snapshot_name]["app_roles"] = [
        role
        for role in missing["before"][snapshot_name]["app_roles"]
        if role["id"] != AGENT_ROLE_ID
    ]
    _rejects(missing)

    changed = _document()
    agent_role = next(
        role
        for role in changed["before"][snapshot_name]["app_roles"]
        if role["id"] == AGENT_ROLE_ID
    )
    agent_role["description"] = "Changed definition"
    _rejects(changed)


@pytest.mark.parametrize(
    "collection",
    [
        "agent_api_resource_assignments",
        "provider_outgoing_assignments",
        "public_outgoing_assignments",
    ],
)
def test_incomplete_assignment_collections_fail_closed(collection: str) -> None:
    document = _document()
    document["before"][collection]["collection_complete"] = False

    _rejects(document)


def test_incomplete_app_role_collection_fails_closed() -> None:
    document = _document()
    document["before"]["application"]["app_roles_collection_complete"] = False

    _rejects(document)


@pytest.mark.parametrize(
    ("collection", "assignment_id"),
    [
        ("agent_api_resource_assignments", SCRIBE_ASSIGNMENT["id"]),
        ("provider_outgoing_assignments", PROVIDER_OTHER_ASSIGNMENT["id"]),
        ("public_outgoing_assignments", PUBLIC_OTHER_ASSIGNMENT["id"]),
    ],
)
def test_verification_preserves_scribe_and_unrelated_assignments(
    collection: str,
    assignment_id: str,
) -> None:
    document = _verification()
    items = document["after"][collection]["items"]
    document["after"][collection]["items"] = [
        item for item in items if item["id"] != assignment_id
    ]

    _rejects(document)


def test_verification_requires_matching_inbound_and_outgoing_views() -> None:
    document = _verification()
    document["after"]["provider_outgoing_assignments"]["items"] = [
        deepcopy(PROVIDER_OTHER_ASSIGNMENT)
    ]

    _rejects(document)


def test_verification_is_bound_to_the_reviewed_plan() -> None:
    document = _verification()
    document["expected_plan_sha256"] = f"sha256_{'f' * 64}"

    _rejects(document)


def test_cli_emits_no_private_identifiers(tmp_path: Path, capsys: Any) -> None:
    document = _document(observed_at=datetime.now(UTC))
    path = tmp_path / "private-plan.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)

    assert m.main(["--input", str(path)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert captured.err == ""
    assert report["status"] == "ready"
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        captured.out,
    )
    for private_value in (
        CANDIDATE_SHA,
        PROVIDER_IDENTITY_NAME,
        PUBLIC_IDENTITY_NAME,
        "/subscriptions/",
        "observed_at",
    ):
        assert private_value not in captured.out


def test_cli_failure_is_fixed_and_value_free(tmp_path: Path, capsys: Any) -> None:
    document = _document(observed_at=datetime.now(UTC))
    document["core_rollout"]["sovereign_client_id"] = _uuid(50)
    path = tmp_path / "private-plan.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)

    assert m.main(["--input", str(path)]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "Production Agent API identity plan refused.\n"
    assert _uuid(50) not in captured.err


def test_cli_requires_exact_private_file_mode(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "private-plan.json"
    path.write_text(
        json.dumps(_document(observed_at=datetime.now(UTC))), encoding="utf-8"
    )
    path.chmod(0o640)

    assert m.main(["--input", str(path)]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "Production Agent API identity plan refused.\n"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_cli_rejects_symlink_input(tmp_path: Path, capsys: Any) -> None:
    target = tmp_path / "target.json"
    target.write_text(
        json.dumps(_document(observed_at=datetime.now(UTC))), encoding="utf-8"
    )
    target.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(target)

    assert m.main(["--input", str(link)]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "Production Agent API identity plan refused.\n"
