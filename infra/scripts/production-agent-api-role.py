#!/usr/bin/env python3
"""Plan one production provider AgentApi.Access connection; never activate Prep.

The existing staging reconciler stays independent. Only a founder's explicit,
fresh exact-plan apply can add the fixed application role and its one provider
assignment. Normal CLI authentication is reused; no credentials are loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
TENANT = "d51754d0-bba5-4810-820e-afac542844d6"
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
AUDIENCE = "af957741-0342-498b-8496-e93706af9e99"
APPLICATION = "352d67fb-1d11-42bd-acee-008d720cae32"
RESOURCE = "a7035858-8f8e-4f5c-a484-3efac710d2ce"
ROLE_ID = "5f7c87d1-b2c1-4e92-9d51-54c3160fdfe6"
GRAPH = "https://graph.microsoft.com/v1.0"
GROUP = "cosmaneura-sovereign-prod"
PREFIX = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}"
PROVIDER_UAI = (
    PREFIX
    + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
    + GROUP
    + "-runtime"
)
PUBLIC_UAI = (
    PREFIX
    + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmaneura-web-prod-runtime"
)
CONTRACT = "cosmaneura.production-provider-agent-role-plan"
ROLE = {
    "allowedMemberTypes": ["Application"],
    "description": "Allows the provider Sovereign workload to call the governed Agent API.",
    "displayName": "Agent API access",
    "id": ROLE_ID,
    "isEnabled": True,
    "value": "AgentApi.Access",
}
ROLE_KEYS = set(ROLE)
ASSIGNMENT_KEYS = {"id", "principalId", "resourceId", "appRoleId", "principalType"}
MAX_JSON = 1024 * 1024
PLAN_LIFETIME = 1800
STAGING_IDS = {
    "8cb2ecb5-3554-4405-be6f-5eda985c2f04",
    "96d232e3-2b2e-416b-91fb-066565efac93",
    "5074cac1-5555-43e9-a469-3e0b7e16608e",
    "b35e8945-c8ee-4e5b-af55-9e1176d7acaf",
}


class Refused(ValueError):
    """A fixed refusal never includes CLI output, credentials or Graph content."""


def require(condition: Any) -> None:
    if not condition:
        raise Refused("production_agent_role_refused")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def decode(raw: bytes) -> Any:
    require(0 < len(raw) <= MAX_JSON)
    return json.loads(raw, object_pairs_hook=unique)


def object_value(value: Any) -> dict[str, Any]:
    require(type(value) is dict)
    return value


def uuid(value: Any) -> str:
    require(type(value) is str)
    parsed = UUID(value)
    require(str(parsed) == value and parsed.int != 0)
    return value


def text(value: Any, limit: int = 4096) -> str:
    require(type(value) is str and 0 < len(value) <= limit and "\x00" not in value)
    return value


@dataclass(frozen=True)
class Targets:
    provider_client_id: str
    provider_principal_id: str
    founder_object_id: str
    audience: str = AUDIENCE

    def validate(self) -> None:
        for value in asdict(self).values():
            uuid(value)
        require(self.audience == AUDIENCE)
        require(self.provider_client_id != self.provider_principal_id)
        require(
            not ({self.provider_client_id, self.provider_principal_id} & STAGING_IDS)
        )
        require(
            self.founder_object_id
            not in {
                self.provider_client_id,
                self.provider_principal_id,
                RESOURCE,
                APPLICATION,
            }
        )


class Client:
    """A bounded normal CLI client. Tests replace it; importing does nothing."""

    def __init__(self, targets: Targets | None = None) -> None:
        self.targets = targets
        self.deadline = time.monotonic() + 300

    def command(self, args: list[str]) -> bytes:
        remaining = self.deadline - time.monotonic()
        require(remaining > 0)
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                args,
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=min(20, remaining),
            )
            require(result.returncode == 0)
            output.seek(0)
            raw = output.read(MAX_JSON + 1)
        require(len(raw) <= MAX_JSON)
        return raw

    def json(self, args: list[str]) -> Any:
        return decode(self.command(args))

    def graph(
        self, path: str, method: str = "GET", body: Any = None, etag: str | None = None
    ) -> Any:
        allowed = {
            "GET": {
                f"applications/{APPLICATION}",
                f"servicePrincipals/{RESOURCE}",
            },
            "PATCH": {f"applications/{APPLICATION}"},
            "POST": set(),
        }
        # IDs in collection paths are fixed or already canonical target UUIDs.
        collection = re.fullmatch(
            r"servicePrincipals/([0-9a-f-]{36})/(appRoleAssignments|appRoleAssignedTo)",
            path,
        )
        principal = re.fullmatch(r"servicePrincipals/([0-9a-f-]{36})", path)
        require((method == "GET") == (body is None))
        if collection is not None:
            uuid(collection[1])
        if principal is not None:
            uuid(principal[1])
        require(
            method in allowed
            and (
                path in allowed[method]
                or (
                    method == "GET"
                    and (collection is not None or principal is not None)
                )
                or (
                    method == "POST"
                    and collection is not None
                    and collection[2] == "appRoleAssignments"
                )
            )
        )
        url = f"{GRAPH}/{path}"
        if method == "GET" and collection:
            url += "?$select=id,principalId,resourceId,appRoleId,principalType"
        elif method == "GET":
            url += "?$select=id,appId,appRoles"
            if not path.startswith("applications/"):
                url += ",servicePrincipalType"
        args = ["az", "rest", "--method", method, "--uri", url, "--only-show-errors"]
        if body is not None:
            require(method in {"PATCH", "POST"})
            if self.targets is None:
                raise Refused("production_agent_role_refused")
            targets = self.targets
            targets.validate()
            if method == "POST":
                require(
                    path
                    == f"servicePrincipals/{targets.provider_principal_id}/appRoleAssignments"
                )
                require(
                    body
                    == {
                        "principalId": targets.provider_principal_id,
                        "resourceId": RESOURCE,
                        "appRoleId": ROLE_ID,
                    }
                    and etag is None
                )
            else:
                require(set(object_value(body)) == {"appRoles"})
                require(agent_role(roles(body["appRoles"])))
            headers = ["Content-Type=application/json"]
            if etag is not None:
                text(etag, 512)
                require("\r" not in etag and "\n" not in etag)
                headers += ["If-Match=" + etag]
            args += ["--headers", *headers, "--body", canonical(body).decode()]
        if method != "GET":
            args += ["--output", "none"]
            self.command(args)
            return None
        return self.json(args + ["--output", "json"])

    def source(self, targets: Targets) -> dict[str, Any]:
        targets.validate()
        user = object_value(
            self.json(
                [
                    "gh",
                    "api",
                    "--hostname",
                    "github.com",
                    "user",
                    "--jq",
                    "{login:.login,type:.type}",
                ]
            )
        )
        require(user == {"login": "JohnClem", "type": "User"})
        require(
            not self.command(
                ["git", "status", "--porcelain", "--untracked-files=all"]
            ).strip()
        )
        head = self.command(["git", "rev-parse", "HEAD"]).decode().strip()
        tree = self.command(["git", "rev-parse", "HEAD^{tree}"]).decode().strip()
        require(
            re.fullmatch("[0-9a-f]{40}", head) and re.fullmatch("[0-9a-f]{40}", tree)
        )
        require(
            self.command(
                ["git", "show", head + ":infra/scripts/production-agent-api-role.py"]
            )
            == Path(__file__).read_bytes()
        )
        remote = object_value(
            self.json(
                [
                    "gh",
                    "api",
                    "--hostname",
                    "github.com",
                    "repos/CosmaNeura/cosmaneura/git/ref/heads/master",
                ]
            )
        )
        require(object_value(remote["object"])["sha"] == head)
        commit = object_value(
            self.json(
                [
                    "gh",
                    "api",
                    "--hostname",
                    "github.com",
                    f"repos/CosmaNeura/cosmaneura/git/commits/{head}",
                ]
            )
        )
        require(object_value(commit["tree"])["sha"] == tree)
        account = object_value(
            self.json(
                [
                    "az",
                    "account",
                    "show",
                    "--query",
                    "{tenantId:tenantId,id:id,userType:user.type}",
                    "--output",
                    "json",
                ]
            )
        )
        require(account == {"tenantId": TENANT, "id": SUBSCRIPTION, "userType": "user"})
        founder = self.json(
            ["az", "ad", "signed-in-user", "show", "--query", "id", "--output", "json"]
        )
        require(uuid(founder) == targets.founder_object_id)
        return {
            "source_head": head,
            "source_tree": tree,
            "founder": targets.founder_object_id,
        }


def roles(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) <= 1000)
    result = []
    ids, values = set(), set()
    for raw in value:
        role = object_value(raw)
        require(set(role) in (ROLE_KEYS, ROLE_KEYS | {"origin"}))
        uuid(role["id"])
        text(role["value"])
        require(role["id"] not in ids and role["value"] not in values)
        ids.add(role["id"])
        values.add(role["value"])
        require(type(role["isEnabled"]) is bool)
        require(type(role["allowedMemberTypes"]) is list and role["allowedMemberTypes"])
        require(all(type(member) is str for member in role["allowedMemberTypes"]))
        require(set(role["allowedMemberTypes"]) <= {"Application", "User"})
        require(len(set(role["allowedMemberTypes"])) == len(role["allowedMemberTypes"]))
        require(
            all(
                type(role[key]) is str and len(role[key]) <= 4096
                for key in ("description", "displayName")
            )
        )
        require("origin" not in role or role["origin"] == "Application")
        result.append(role)
    return sorted(result, key=lambda item: item["id"])


def agent_role(values: list[dict[str, Any]]) -> bool:
    matching = [
        role
        for role in values
        if role["id"] == ROLE_ID or role["value"] == ROLE["value"]
    ]
    require(len(matching) <= 1)
    if matching:
        require({key: matching[0][key] for key in ROLE_KEYS} == ROLE)
    return bool(matching)


def assignments(value: Any, principal: str | None = None) -> list[dict[str, Any]]:
    payload = object_value(value)
    require("@odata.nextLink" not in payload and type(payload.get("value")) is list)
    require(len(payload["value"]) <= 1000)
    result, ids = [], set()
    for raw in payload["value"]:
        item = object_value(raw)
        require(set(item) == ASSIGNMENT_KEYS)
        text(item["id"], 160)
        require(item["id"] not in ids)
        ids.add(item["id"])
        for key in ("principalId", "resourceId"):
            uuid(item[key])
        # Graph's default assignment uses zero, but never matches the fixed role.
        role_id = item["appRoleId"]
        require(type(role_id) is str and str(UUID(role_id)) == role_id)
        require(item["principalType"] in {"ServicePrincipal", "User", "Group"})
        if principal is not None:
            require(
                item["principalId"] == principal
                and item["principalType"] == "ServicePrincipal"
            )
        result.append(item)
    return sorted(result, key=lambda item: item["id"])


def matching_assignments(
    values: list[dict[str, Any]], principal: str | None = None
) -> list[dict[str, Any]]:
    return [
        item
        for item in values
        if item["resourceId"] == RESOURCE
        and item["appRoleId"] == ROLE_ID
        and (principal is None or item["principalId"] == principal)
    ]


def other_roles(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [role for role in values if role["id"] != ROLE_ID]


def snapshot(
    client: Client, targets: Targets, *, target_assignment_lag: bool = False
) -> dict[str, Any]:
    identities = []
    for resource in (PROVIDER_UAI, PUBLIC_UAI):
        identity = object_value(
            client.json(
                [
                    "az",
                    "identity",
                    "show",
                    "--ids",
                    resource,
                    "--query",
                    "{id:id,tenantId:tenantId,clientId:clientId,principalId:principalId}",
                    "--output",
                    "json",
                ]
            )
        )
        require(identity["id"] == resource and identity["tenantId"] == TENANT)
        uuid(identity["clientId"])
        uuid(identity["principalId"])
        identities.append(identity)
        principal = object_value(
            client.graph(f"servicePrincipals/{identity['principalId']}")
        )
        require(
            principal["id"] == identity["principalId"]
            and principal["appId"] == identity["clientId"]
            and principal["servicePrincipalType"] == "ManagedIdentity"
        )
    provider, public = identities
    require(
        provider["clientId"] == targets.provider_client_id
        and provider["principalId"] == targets.provider_principal_id
    )
    require(
        len({item[key] for item in identities for key in ("clientId", "principalId")})
        == 4
    )
    app = object_value(client.graph(f"applications/{APPLICATION}"))
    resource = object_value(client.graph(f"servicePrincipals/{RESOURCE}"))
    require(app["id"] == APPLICATION and app["appId"] == targets.audience)
    require(
        resource["id"] == RESOURCE
        and resource["appId"] == targets.audience
        and resource["servicePrincipalType"] == "Application"
    )
    app_roles, resource_roles = roles(app["appRoles"]), roles(resource["appRoles"])
    app_ready, resource_ready = agent_role(app_roles), agent_role(resource_roles)
    require(app_ready or not resource_ready)
    require(other_roles(app_roles) == other_roles(resource_roles))
    provider_assignments = assignments(
        client.graph(
            f"servicePrincipals/{targets.provider_principal_id}/appRoleAssignments"
        ),
        targets.provider_principal_id,
    )
    public_assignments = assignments(
        client.graph(f"servicePrincipals/{public['principalId']}/appRoleAssignments"),
        public["principalId"],
    )
    grants = assignments(
        client.graph(f"servicePrincipals/{RESOURCE}/appRoleAssignedTo")
    )
    require(all(item["resourceId"] == RESOURCE for item in grants))
    require(not matching_assignments(public_assignments))
    selected = matching_assignments(provider_assignments)
    require(len(selected) <= 1 and (not selected or app_ready and resource_ready))
    projected = matching_assignments(grants, targets.provider_principal_id)
    require(len(projected) <= 1 and (not projected or app_ready and resource_ready))
    # Only one missing target view may lag after this invocation's single POST.
    # Conflicting visible IDs/metadata and duplicate assignments never retry.
    require(
        projected == selected
        or (target_assignment_lag and (not projected or not selected))
    )
    require(not matching_assignments(grants, public["principalId"]))
    require(
        all(
            item["principalType"] == "ServicePrincipal"
            for item in matching_assignments(grants)
        )
    )
    etag = app.get("@odata.etag")
    if etag is not None:
        text(etag, 512)
        require("\r" not in etag and "\n" not in etag)
    return {
        "identities": identities,
        "roles": app_roles,
        "resource_roles": resource_roles,
        "provider_assignments": provider_assignments,
        "public_assignments": public_assignments,
        "grants": grants,
        "etag": etag,
    }


def actions(state: dict[str, Any]) -> list[str]:
    result = []
    if not agent_role(state["roles"]):
        result.append("add_fixed_application_role")
    if not agent_role(state["resource_roles"]):
        result.append("wait_for_fixed_role_projection")
    if not matching_assignments(state["provider_assignments"]):
        result.append("assign_fixed_role_to_production_provider")
    return result


def prepare(client: Client, targets: Targets, now: int) -> dict[str, Any]:
    require(type(now) is int and now > 0)
    source = client.source(targets)
    state = snapshot(client, targets)
    require(client.source(targets) == source)
    return {
        "contract": CONTRACT,
        "version": 1,
        "source": source,
        "targets": asdict(targets),
        "created_at": now,
        "expires_at": now + PLAN_LIFETIME,
        "before_digest": digest(state),
        "actions": actions(state),
        "fixed_role": ROLE,
        "scope": "production-provider-agent-api-only",
    }


def preserve(before: dict[str, Any], after: dict[str, Any], targets: Targets) -> None:
    require(before["identities"] == after["identities"])
    require(other_roles(before["roles"]) == other_roles(after["roles"]))
    require(
        other_roles(before["resource_roles"]) == other_roles(after["resource_roles"])
    )

    def without_assignment(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            item
            for item in values
            if not (
                item["resourceId"] == RESOURCE
                and item["appRoleId"] == ROLE_ID
                and item["principalId"] == targets.provider_principal_id
            )
        ]

    require(
        without_assignment(before["provider_assignments"])
        == without_assignment(after["provider_assignments"])
    )
    require(before["public_assignments"] == after["public_assignments"])
    require(without_assignment(before["grants"]) == without_assignment(after["grants"]))
    for key in ("provider_assignments", "grants"):
        existing = matching_assignments(before[key], targets.provider_principal_id)
        if existing:
            require(
                matching_assignments(after[key], targets.provider_principal_id)
                == existing
            )


def apply(
    client: Client,
    targets: Targets,
    plan: dict[str, Any],
    now: int,
    confirm_production: bool,
) -> dict[str, Any]:
    require(confirm_production is True)
    require(
        set(plan)
        == {
            "contract",
            "version",
            "source",
            "targets",
            "created_at",
            "expires_at",
            "before_digest",
            "actions",
            "fixed_role",
            "scope",
        }
    )
    require(
        plan["contract"] == CONTRACT
        and type(plan["version"]) is int
        and plan["version"] == 1
    )
    require(
        plan["targets"] == asdict(targets)
        and plan["fixed_role"] == ROLE
        and plan["scope"] == "production-provider-agent-api-only"
    )
    require(
        type(plan["created_at"]) is int
        and type(plan["expires_at"]) is int
        and type(now) is int
    )
    require(
        plan["created_at"]
        <= now
        < plan["expires_at"]
        == plan["created_at"] + PLAN_LIFETIME
    )
    require(client.source(targets) == plan["source"])
    before = snapshot(client, targets)
    require(
        digest(before) == plan["before_digest"] and actions(before) == plan["actions"]
    )
    require(client.source(targets) == plan["source"])
    current = snapshot(client, targets)
    require(current == before)
    if not agent_role(before["roles"]):
        payload = {
            "appRoles": [
                {key: role[key] for key in ROLE_KEYS} for role in before["roles"]
            ]
            + [ROLE]
        }
        require(plan["created_at"] <= time.time() < plan["expires_at"])
        client.graph(f"applications/{APPLICATION}", "PATCH", payload, before["etag"])
    for attempt in range(6):
        current = snapshot(client, targets)
        preserve(before, current, targets)
        require(agent_role(current["roles"]))
        if agent_role(current["resource_roles"]):
            break
        require(attempt < 5)
        time.sleep(2)
    require(client.source(targets) == plan["source"])
    immediately_before = snapshot(client, targets)
    require(immediately_before == current)
    posted = not matching_assignments(current["provider_assignments"])
    if posted:
        body = {
            "principalId": targets.provider_principal_id,
            "resourceId": RESOURCE,
            "appRoleId": ROLE_ID,
        }
        require(plan["created_at"] <= time.time() < plan["expires_at"])
        client.graph(
            f"servicePrincipals/{targets.provider_principal_id}/appRoleAssignments",
            "POST",
            body,
        )
    after = current
    observed_target: dict[str, Any] | None = None
    for attempt in range(6):
        after = snapshot(client, targets, target_assignment_lag=posted)
        preserve(before, after, targets)
        require(agent_role(after["roles"]) and agent_role(after["resource_roles"]))
        selected = matching_assignments(after["provider_assignments"])
        projected = matching_assignments(after["grants"], targets.provider_principal_id)
        visible = selected + projected
        if visible:
            if observed_target is None:
                observed_target = visible[0]
            require(all(item == observed_target for item in visible))
        if len(selected) == 1 and projected == selected:
            break
        require(posted and attempt < 5)
        time.sleep(2)
    require(client.source(targets) == plan["source"])
    return {
        "contract": CONTRACT,
        "version": 1,
        "plan_digest": digest(plan),
        "source": plan["source"],
        "targets": plan["targets"],
        "after_digest": digest(after),
        "assignment_count": 1,
        "other_roles_and_assignments_preserved": True,
        "session_prep_activated": False,
    }


def load_plan(path: Path, expected_digest: str) -> dict[str, Any]:
    require(re.fullmatch("[0-9a-f]{64}", expected_digest))
    require(path.is_absolute() and path.resolve() == path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
            and not info.st_mode & 0o022
        )
        raw = source.read(MAX_JSON + 1)
    require(hashlib.sha256(raw).hexdigest() == expected_digest)
    return object_value(decode(raw))


def save(path: Path, value: Any) -> None:
    require(path.is_absolute() and path.parent.resolve() == path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(canonical(value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("plan", "apply"), default="plan")
    parser.add_argument("--provider-client-id", required=True)
    parser.add_argument("--provider-principal-id", required=True)
    parser.add_argument("--founder-object-id", required=True)
    parser.add_argument("--agent-api-audience", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--confirm-production", action="store_true")
    args = parser.parse_args()
    try:
        targets = Targets(
            args.provider_client_id,
            args.provider_principal_id,
            args.founder_object_id,
            args.agent_api_audience,
        )
        targets.validate()
        require(
            args.output.is_absolute()
            and args.output.parent.resolve() == args.output.parent
            and not args.output.exists()
        )
        now = int(time.time())
        client = Client(targets)
        if args.mode == "plan":
            require(
                args.plan is None
                and args.plan_sha256 is None
                and not args.confirm_production
            )
            result = prepare(client, targets, now)
        else:
            require(
                args.plan is not None
                and args.plan_sha256 is not None
                and args.confirm_production
            )
            result = apply(
                client,
                targets,
                load_plan(args.plan, args.plan_sha256),
                now,
                args.confirm_production,
            )
            result["plan_file_sha256"] = args.plan_sha256
        save(args.output, result)
        print("production_agent_role_" + args.mode + "_complete")
        return 0
    except (
        Refused,
        ValueError,
        TypeError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
    ):
        print("production_agent_role_refused", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
