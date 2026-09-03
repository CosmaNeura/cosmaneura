#!/usr/bin/env python3
"""Provision or remove one fail-closed, isolated PostgreSQL restore envelope.

The controller intentionally emits only bounded error codes and aggregate,
non-secret receipts. Azure responses are never copied to stdout or stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "contracts/staging-rehearsal/v1/restore-envelope-policy.json"
POLICY_SHA256 = "e9d894ef8418c092d61539ddd02e434d8863af4923cdae86468064fd0d65163c"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REHEARSAL_RE = re.compile(r"^[0-9a-f]{12}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
BASE_RE = re.compile(r"^(cnr-[0-9a-f]{12}-e([0-9]{10}))-(pg|vnet|pep|nic|dnslink)$")
PLACEHOLDER_RE = re.compile(r"^CONFIGURE_EXACT_")
ALLOWED_ACTIONS = frozenset({"validate-policy", "provision", "cleanup", "sweep"})


class EnvelopeError(Exception):
    """A bounded, content-free refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise EnvelopeError(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("invalid_json_file")
    if not isinstance(value, dict):
        _fail("invalid_json_object")
    return value


def _id(*parts: str) -> str:
    return "/" + "/".join(part.strip("/") for part in parts)


def _lower_id(value: object) -> str:
    return str(value or "").rstrip("/").lower()


def _location(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_hex(value: str, pattern: re.Pattern[str], code: str) -> str:
    if not pattern.fullmatch(value):
        _fail(code)
    return value


def _validate_policy(policy: dict[str, Any], *, executable: bool) -> None:
    if _sha256(POLICY_PATH.read_bytes()) != POLICY_SHA256:
        _fail("policy_digest_mismatch")
    if (
        policy.get("schema_version") != 1
        or policy.get("policy_id") != "cosmaneura-staging-restore-envelope-v1"
    ):
        _fail("policy_identity_mismatch")
    if (
        policy.get("repository") != "CosmaNeura/cosmaneura"
        or policy.get("protected_ref") != "refs/heads/master"
        or policy.get("founder_actor") != "JohnClem"
        or not isinstance(policy.get("trusted_evidence_binding_implemented"), bool)
    ):
        _fail("policy_repository_mismatch")
    source = policy.get("source", {})
    target = policy.get("target", {})
    if source != {
        "resource_group": "cosmaneura-staging",
        "server_name": "cosmaneura-staging-postgres",
        "database_name": "staging",
        "fqdn": "cosmaneura-staging-postgres.postgres.database.azure.com",
        "version": "16",
        "public_network_access": "Enabled",
        "backup_retention_days": 7,
        "firewall_rules": [
            ["aca-staging-egress", "130.131.32.65", "130.131.32.65"],
            ["allow-access-from-azure-services", "0.0.0.0", "0.0.0.0"],
        ],
        "inventory_profile": "pre",
        "inventory_revision": "89c31f0ae275",
    }:
        _fail("policy_source_mismatch")
    expected_target = {
        "resource_group": "cosmaneura-staging-rehearsal",
        "name_prefix": "cnr",
        "default_ttl_hours": 6,
        "maximum_ttl_hours": 24,
        "vnet_address_prefix": "10.254.0.0/24",
        "private_endpoint_subnet_prefix": "10.254.0.0/27",
        "private_endpoint_subnet_name": "private-endpoints",
        "private_dns_zone": "privatelink.postgres.database.azure.com",
        "postgres_group_id": "postgresqlServer",
        "required_tags": {
            "environment": "staging-restore-rehearsal",
            "data-classification": "restricted-restored-copy",
            "managed-by": "staging-restore-envelope-v1",
        },
    }
    if target != expected_target:
        _fail("policy_target_mismatch")
    tenant = str(policy.get("tenant_id", ""))
    subscription = str(policy.get("subscription_id", ""))
    if executable and (
        not UUID_RE.fullmatch(tenant) or not UUID_RE.fullmatch(subscription)
    ):
        _fail("policy_identifiers_unconfigured")
    identities = policy.get("identities", {})
    federation = policy.get("federation", {})
    deny = policy.get("deny_policy", {})
    role_digests: list[str] = []
    role_ids: list[str] = []
    identity_ids: list[str] = []
    values = [
        tenant,
        subscription,
        str(deny.get("definition_id", "")),
        str(deny.get("canonical_rule_sha256", "")),
    ]
    for kind in ("provision", "cleanup"):
        identity = identities.get(kind, {})
        if identity.get("environment") != f"staging-restore-{kind}":
            _fail("policy_identity_invalid")
        identity_ids.extend(
            [str(identity.get("client_id", "")), str(identity.get("principal_id", ""))]
        )
        for role_kind in ("target", "source", "control"):
            ids = [
                str(item)
                for item in identity.get(
                    f"required_{role_kind}_role_definition_ids", []
                )
            ]
            digests = [
                str(item)
                for item in identity.get(
                    f"required_{role_kind}_role_permissions_sha256", []
                )
            ]
            if executable and len(ids) != len(digests):
                _fail("policy_role_definition_mismatch")
            if executable and role_kind in {"target", "control"} and not ids:
                _fail("policy_role_definition_mismatch")
            if executable and kind == "provision" and role_kind == "source" and not ids:
                _fail("policy_role_definition_mismatch")
            role_ids.extend(ids)
            role_digests.extend(digests)
    values.extend(role_ids)
    values.extend(role_digests)
    values.extend(identity_ids)
    if executable and any(not value or PLACEHOLDER_RE.match(value) for value in values):
        _fail("policy_placeholders_unconfigured")
    if executable and any(not UUID_RE.fullmatch(value) for value in identity_ids):
        _fail("policy_identity_invalid")
    if executable and any(
        not re.fullmatch(r"[0-9a-f]{64}", value) for value in role_digests
    ):
        _fail("policy_role_digest_invalid")
    role_prefix = f"/subscriptions/{subscription}/providers/microsoft.authorization/roledefinitions/"
    if executable and any(
        not _lower_id(value).startswith(role_prefix)
        or not UUID_RE.fullmatch(_lower_id(value).removeprefix(role_prefix))
        for value in role_ids
    ):
        _fail("policy_role_definition_mismatch")
    if deny.get("assignment_name") != "deny-staging-restore-firewall-rules":
        _fail("policy_deny_assignment_mismatch")
    rule_digest = str(deny.get("canonical_rule_sha256", ""))
    if executable and not re.fullmatch(r"[0-9a-f]{64}", rule_digest):
        _fail("policy_deny_digest_invalid")
    deny_prefix = f"/subscriptions/{subscription}/providers/microsoft.authorization/policydefinitions/"
    if executable and (
        not _lower_id(deny.get("definition_id")).startswith(deny_prefix)
        or "/" in _lower_id(deny.get("definition_id")).removeprefix(deny_prefix)
    ):
        _fail("policy_deny_definition_invalid")
    if federation != {
        "issuer": "https://token.actions.githubusercontent.com",
        "audiences": ["api://AzureADTokenExchange"],
        "provision_subject": "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision",
        "cleanup_subject": "repo:CosmaNeura/cosmaneura:environment:staging-restore-cleanup",
    }:
        _fail("policy_federation_mismatch")
    if policy.get("github_environment_controls") != {
        "deployment_branch": "master",
        "protected_branches_only": True,
        "provision_required_reviewers": ["JohnClem"],
        "cleanup_required_reviewers": [],
    }:
        _fail("policy_environment_controls_mismatch")


class Azure:
    """Minimal JSON-only Azure CLI adapter with sanitized failures."""

    def run(self, *arguments: str, allow_empty: bool = False) -> Any:
        command = ["az", *arguments, "--only-show-errors", "--output", "json"]
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=1800
            )
        except (OSError, subprocess.SubprocessError):
            _fail("azure_cli_unavailable")
        if result.returncode != 0:
            _fail("azure_operation_failed")
        if allow_empty and not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            _fail("azure_response_invalid")


@dataclass(frozen=True)
class Context:
    policy: dict[str, Any]
    subscription: str
    target_group_id: str
    source_server_id: str

    @classmethod
    def from_policy(cls, policy: dict[str, Any]) -> Context:
        subscription = policy["subscription_id"]
        source = policy["source"]
        target = policy["target"]
        return cls(
            policy=policy,
            subscription=subscription,
            target_group_id=_id(
                "subscriptions",
                subscription,
                "resourceGroups",
                target["resource_group"],
            ),
            source_server_id=_id(
                "subscriptions",
                subscription,
                "resourceGroups",
                source["resource_group"],
                "providers/Microsoft.DBforPostgreSQL/flexibleServers",
                source["server_name"],
            ),
        )

    def resource_id(self, provider_type: str, name: str) -> str:
        return _id(self.target_group_id, "providers", provider_type, name)


def _assert_repo_context(policy: dict[str, Any], args: argparse.Namespace) -> None:
    repository = args.repository
    ref = args.ref
    sha = args.workflow_sha
    if repository != policy["repository"] or ref != policy["protected_ref"]:
        _fail("unprotected_repository_context")
    _require_hex(sha, SHA_RE, "invalid_workflow_sha")
    if args.event_name not in {"workflow_dispatch", "schedule"}:
        _fail("unsupported_workflow_event")
    if args.event_name == "workflow_dispatch" and (
        args.actor != policy["founder_actor"]
        or args.triggering_actor != policy["founder_actor"]
    ):
        _fail("founder_authorization_required")
    intended_action = (
        args.intended_action if args.action == "validate-policy" else args.action
    )
    if args.event_name == "schedule" and intended_action != "sweep":
        _fail("scheduled_action_mismatch")
    if (
        intended_action == "provision"
        and policy["trusted_evidence_binding_implemented"] is not True
    ):
        _fail("trusted_evidence_binding_not_implemented")


def _preflight_identity(az: Azure, ctx: Context, kind: str) -> dict[str, Any]:
    policy = ctx.policy
    identity = policy["identities"][kind]
    account = az.run("account", "show")
    if (
        account.get("tenantId") != policy["tenant_id"]
        or account.get("id") != ctx.subscription
        or account.get("user", {}).get("type") != "servicePrincipal"
        or account.get("user", {}).get("name") != identity["client_id"]
    ):
        _fail("azure_identity_mismatch")
    service_principal = az.run("ad", "sp", "show", "--id", identity["client_id"])
    if (
        service_principal.get("appId") != identity["client_id"]
        or service_principal.get("id") != identity["principal_id"]
    ):
        _fail("azure_service_principal_mismatch")
    group = az.run("group", "show", "--name", policy["target"]["resource_group"])
    if _lower_id(group.get("id")) != _lower_id(ctx.target_group_id) or _location(
        group.get("location")
    ) != _location(policy["location"]):
        _fail("target_resource_group_mismatch")
    if kind == "cleanup":
        return identity
    principal = identity["principal_id"]
    subscription_scope = _id("subscriptions", ctx.subscription)
    control_roles = [
        (subscription_scope, role_id)
        for role_id in identity["required_control_role_definition_ids"]
    ]
    target_roles = [
        (ctx.target_group_id, role_id)
        for role_id in identity["required_target_role_definition_ids"]
    ] + control_roles
    source_roles = [
        (ctx.source_server_id, role_id)
        for role_id in identity["required_source_role_definition_ids"]
    ] + control_roles
    for scope, required in (
        (ctx.target_group_id, target_roles),
        (ctx.source_server_id, source_roles),
    ):
        assignments = az.run(
            "role",
            "assignment",
            "list",
            "--assignee-object-id",
            principal,
            "--scope",
            scope,
            "--include-inherited",
            "--include-groups",
        )
        _require_roles(assignments, required, principal)
    for role_kind in ("target", "source", "control"):
        role_ids = identity.get(f"required_{role_kind}_role_definition_ids", [])
        if role_ids:
            _verify_role_definitions(
                az,
                role_ids,
                identity[f"required_{role_kind}_role_permissions_sha256"],
            )
    credentials = az.run(
        "ad", "app", "federated-credential", "list", "--id", identity["client_id"]
    )
    if not isinstance(credentials, list):
        _fail("federated_credential_mismatch")
    expected_subject = policy["federation"][f"{kind}_subject"]
    matches = [
        item
        for item in credentials
        if item.get("issuer") == policy["federation"]["issuer"]
        and item.get("subject") == expected_subject
        and item.get("audiences") == policy["federation"]["audiences"]
    ]
    if len(credentials) != 1 or len(matches) != 1:
        _fail("federated_credential_mismatch")
    _verify_deny_policy(az, ctx)
    return identity


def _require_roles(
    actual: Any, required: list[tuple[str, str]], principal: str
) -> None:
    if not isinstance(actual, list) or not required:
        _fail("role_assignment_mismatch")
    if any(
        not isinstance(item, dict) or item.get("principalId") != principal
        for item in actual
    ):
        _fail("role_assignment_mismatch")
    found = {
        (_lower_id(item.get("scope")), _lower_id(item.get("roleDefinitionId")))
        for item in actual
    }
    expected = {(_lower_id(scope), _lower_id(role)) for scope, role in required}
    if found != expected:
        _fail("role_assignment_mismatch")


def _role_permissions_digest(definition: dict[str, Any]) -> str:
    permissions = definition.get("permissions")
    if not isinstance(permissions, list) or not all(
        isinstance(item, dict) for item in permissions
    ):
        _fail("role_definition_mismatch")
    keys = ("actions", "notActions", "dataActions", "notDataActions")
    normalized = [
        {key: sorted(item.get(key) or []) for key in keys} for item in permissions
    ]
    return _sha256(_canonical(sorted(normalized, key=_canonical)))


def _verify_role_definitions(
    az: Azure, required: list[str], digests: list[str]
) -> None:
    if len(required) != len(digests) or not required:
        _fail("role_definition_mismatch")
    for role_id, digest in zip(required, digests, strict=True):
        definition = az.run("role", "definition", "show", "--id", role_id)
        if (
            not isinstance(definition, dict)
            or _lower_id(definition.get("id")) != _lower_id(role_id)
            or _role_permissions_digest(definition) != digest
        ):
            _fail("role_definition_mismatch")


def _verify_deny_policy(az: Azure, ctx: Context) -> None:
    expected = ctx.policy["deny_policy"]
    assignment = az.run(
        "policy",
        "assignment",
        "show",
        "--name",
        expected["assignment_name"],
        "--scope",
        ctx.target_group_id,
    )
    if (
        _lower_id(assignment.get("scope")) != _lower_id(ctx.target_group_id)
        or _lower_id(assignment.get("policyDefinitionId"))
        != _lower_id(expected["definition_id"])
        or assignment.get("enforcementMode", "Default") != "Default"
        or assignment.get("notScopes") not in (None, [])
        or assignment.get("parameters") not in (None, {})
        or assignment.get("overrides") not in (None, [])
        or assignment.get("resourceSelectors") not in (None, [])
    ):
        _fail("deny_policy_assignment_mismatch")
    if az.run("policy", "exemption", "list", "--scope", ctx.target_group_id) != []:
        _fail("deny_policy_exemption_present")
    prefix = (
        _lower_id(
            _id(
                "subscriptions",
                ctx.subscription,
                "providers/Microsoft.Authorization/policyDefinitions",
            )
        )
        + "/"
    )
    definition_id = _lower_id(expected["definition_id"])
    if not definition_id.startswith(prefix) or "/" in definition_id.removeprefix(
        prefix
    ):
        _fail("deny_policy_definition_id_mismatch")
    definition = az.run(
        "policy",
        "definition",
        "show",
        "--name",
        definition_id.removeprefix(prefix),
        "--subscription",
        ctx.subscription,
    )
    rule = definition.get("policyRule")
    if (
        definition.get("mode") != "All"
        or not isinstance(rule, dict)
        or _sha256(_canonical(rule)) != expected["canonical_rule_sha256"]
    ):
        _fail("deny_policy_rule_mismatch")


def _names(
    policy: dict[str, Any], rehearsal_id: str, expires: datetime
) -> dict[str, str]:
    _require_hex(rehearsal_id, REHEARSAL_RE, "invalid_rehearsal_id")
    base = (
        f"{policy['target']['name_prefix']}-{rehearsal_id}-e{int(expires.timestamp())}"
    )
    return {
        "base": base,
        "server": f"{base}-pg",
        "vnet": f"{base}-vnet",
        "pe": f"{base}-pep",
        "nic": f"{base}-nic",
        "link": f"{base}-dnslink",
    }


def _tags(ctx: Context, rehearsal_id: str, expires: datetime) -> list[str]:
    tags = dict(ctx.policy["target"]["required_tags"])
    tags.update(
        {"rehearsal-id": rehearsal_id, "expires-epoch": str(int(expires.timestamp()))}
    )
    return [f"{key}={value}" for key, value in sorted(tags.items())]


def _allowed_ids(ctx: Context, names: dict[str, str]) -> set[str]:
    zone = ctx.policy["target"]["private_dns_zone"]
    zone_id = ctx.resource_id("Microsoft.Network/privateDnsZones", zone)
    pe_id = ctx.resource_id("Microsoft.Network/privateEndpoints", names["pe"])
    return {
        _lower_id(
            ctx.resource_id(
                "Microsoft.DBforPostgreSQL/flexibleServers", names["server"]
            )
        ),
        _lower_id(ctx.resource_id("Microsoft.Network/virtualNetworks", names["vnet"])),
        _lower_id(ctx.resource_id("Microsoft.Network/privateEndpoints", names["pe"])),
        _lower_id(ctx.resource_id("Microsoft.Network/networkInterfaces", names["nic"])),
        _lower_id(zone_id),
        _lower_id(_id(zone_id, "virtualNetworkLinks", names["link"])),
        _lower_id(_id(pe_id, "privateDnsZoneGroups", "default")),
    }


def _inventory(az: Azure, ctx: Context) -> list[dict[str, Any]]:
    resources = az.run(
        "resource", "list", "--resource-group", ctx.policy["target"]["resource_group"]
    )
    if not isinstance(resources, list):
        _fail("resource_inventory_invalid")
    return resources


def _require_empty_group(az: Azure, ctx: Context) -> None:
    if _inventory(az, ctx):
        _fail("target_resource_group_not_empty")


def _tag(az: Azure, resource_id: str, tags: list[str]) -> None:
    az.run("resource", "tag", "--ids", resource_id, "--tags", *tags)


def _verify_source(az: Azure, ctx: Context, restore_time: datetime) -> None:
    source = az.run(
        "postgres", "flexible-server", "show", "--ids", ctx.source_server_id
    )
    expected = ctx.policy["source"]
    if (
        _lower_id(source.get("id")) != _lower_id(ctx.source_server_id)
        or source.get("name") != expected["server_name"]
        or source.get("fullyQualifiedDomainName") != expected["fqdn"]
        or _location(source.get("location")) != _location(ctx.policy["location"])
        or source.get("version") != expected["version"]
        or source.get("network", {}).get("publicNetworkAccess")
        != expected["public_network_access"]
        or source.get("backup", {}).get("backupRetentionDays")
        != expected["backup_retention_days"]
    ):
        _fail("source_server_mismatch")
    try:
        earliest = datetime.fromisoformat(
            str(source["backup"]["earliestRestoreDate"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        _fail("source_restore_window_invalid")
    if restore_time < earliest or restore_time > _now():
        _fail("restore_time_outside_window")
    source_rules = az.run(
        "postgres",
        "flexible-server",
        "firewall-rule",
        "list",
        "--resource-group",
        expected["resource_group"],
        "--server-name",
        expected["server_name"],
    )
    actual_rules = sorted(
        [item.get("name"), item.get("startIpAddress"), item.get("endIpAddress")]
        for item in source_rules
    )
    if actual_rules != expected["firewall_rules"]:
        _fail("source_firewall_rules_mismatch")


def _verify_zero_firewalls(
    az: Azure, ctx: Context, server: str, *, locked: bool = False
) -> None:
    if locked:
        server_id = ctx.resource_id("Microsoft.DBforPostgreSQL/flexibleServers", server)
        result = az.run(
            "rest",
            "--method",
            "get",
            "--url",
            f"https://management.azure.com{server_id}/firewallRules?api-version=2024-08-01",
        )
        if not isinstance(result, dict) or result.get("nextLink") not in (None, ""):
            _fail("target_firewall_inventory_incomplete")
        rules = result.get("value")
    else:
        rules = az.run(
            "postgres",
            "flexible-server",
            "firewall-rule",
            "list",
            "--resource-group",
            ctx.policy["target"]["resource_group"],
            "--server-name",
            server,
        )
    if rules != []:
        _fail("target_firewall_rule_present")


def _verify_resource_set(az: Azure, ctx: Context, names: dict[str, str]) -> None:
    actual = {_lower_id(item.get("id")) for item in _inventory(az, ctx)}
    allowed = _allowed_ids(ctx, names)
    if not actual or actual - allowed:
        _fail("unknown_target_resource")
    nic_id = _lower_id(
        ctx.resource_id("Microsoft.Network/networkInterfaces", names["nic"])
    )
    required = {
        _lower_id(
            ctx.resource_id(
                "Microsoft.DBforPostgreSQL/flexibleServers", names["server"]
            )
        ),
        _lower_id(ctx.resource_id("Microsoft.Network/virtualNetworks", names["vnet"])),
        _lower_id(ctx.resource_id("Microsoft.Network/privateEndpoints", names["pe"])),
        nic_id,
        _lower_id(
            ctx.resource_id(
                "Microsoft.Network/privateDnsZones",
                ctx.policy["target"]["private_dns_zone"],
            )
        ),
    }
    if required - actual:
        _fail("target_resource_missing")
    match = BASE_RE.fullmatch(names["server"])
    if match is None:
        _fail("invalid_target_name")
    rehearsal_id = names["base"].split("-")[1]
    expiry = match.group(2)
    for item in _inventory(az, ctx):
        if _lower_id(item.get("id")) in required - {nic_id}:
            tags = item.get("tags") or {}
            expected_tags = {
                **ctx.policy["target"]["required_tags"],
                "rehearsal-id": rehearsal_id,
                "expires-epoch": expiry,
            }
            if any(tags.get(key) != value for key, value in expected_tags.items()):
                _fail("target_resource_tags_mismatch")


def _verify_private_endpoint(
    az: Azure,
    ctx: Context,
    names: dict[str, str],
    target_id: str,
    *,
    require_approved: bool,
) -> str:
    pe = az.run(
        "network",
        "private-endpoint",
        "show",
        "--resource-group",
        ctx.policy["target"]["resource_group"],
        "--name",
        names["pe"],
    )
    connections = pe.get("privateLinkServiceConnections") or []
    nic_id = ctx.resource_id("Microsoft.Network/networkInterfaces", names["nic"])
    subnet_id = _id(
        ctx.resource_id("Microsoft.Network/virtualNetworks", names["vnet"]),
        "subnets",
        ctx.policy["target"]["private_endpoint_subnet_name"],
    )
    if (
        len(connections) != 1
        or _lower_id(pe.get("subnet", {}).get("id")) != _lower_id(subnet_id)
        or _lower_id(connections[0].get("privateLinkServiceId")) != _lower_id(target_id)
        or connections[0].get("groupIds") != [ctx.policy["target"]["postgres_group_id"]]
        or (
            require_approved
            and connections[0]
            .get("privateLinkServiceConnectionState", {})
            .get("status")
            != "Approved"
        )
        or [_lower_id(item.get("id")) for item in pe.get("networkInterfaces", [])]
        != [_lower_id(nic_id)]
    ):
        _fail("private_endpoint_connection_mismatch")
    return nic_id


def _provision_network(
    az: Azure, ctx: Context, names: dict[str, str], tags: list[str], target_id: str
) -> dict[str, str]:
    target = ctx.policy["target"]
    group = target["resource_group"]
    az.run(
        "network",
        "vnet",
        "create",
        "--resource-group",
        group,
        "--name",
        names["vnet"],
        "--location",
        ctx.policy["location"],
        "--address-prefixes",
        target["vnet_address_prefix"],
        "--subnet-name",
        target["private_endpoint_subnet_name"],
        "--subnet-prefixes",
        target["private_endpoint_subnet_prefix"],
        "--tags",
        *tags,
    )
    vnet_id = ctx.resource_id("Microsoft.Network/virtualNetworks", names["vnet"])
    vnet = az.run(
        "network", "vnet", "show", "--resource-group", group, "--name", names["vnet"]
    )
    subnets = vnet.get("subnets") or []
    if (
        vnet.get("addressSpace", {}).get("addressPrefixes")
        != [target["vnet_address_prefix"]]
        or vnet.get("dhcpOptions", {}).get("dnsServers") not in (None, [])
        or [item.get("name") for item in subnets]
        != [target["private_endpoint_subnet_name"]]
    ):
        _fail("target_vnet_address_mismatch")
    az.run(
        "network",
        "vnet",
        "subnet",
        "update",
        "--resource-group",
        group,
        "--vnet-name",
        names["vnet"],
        "--name",
        target["private_endpoint_subnet_name"],
        "--private-endpoint-network-policies",
        "Disabled",
        "--default-outbound-access",
        "false",
    )
    subnet = az.run(
        "network",
        "vnet",
        "subnet",
        "show",
        "--resource-group",
        group,
        "--vnet-name",
        names["vnet"],
        "--name",
        target["private_endpoint_subnet_name"],
    )
    if (
        subnet.get("addressPrefix") != target["private_endpoint_subnet_prefix"]
        or subnet.get("privateEndpointNetworkPolicies") != "Disabled"
        or subnet.get("defaultOutboundAccess") is not False
        or subnet.get("routeTable") is not None
        or subnet.get("natGateway") is not None
        or subnet.get("networkSecurityGroup") is not None
        or subnet.get("serviceEndpoints") not in (None, [])
        or subnet.get("serviceEndpointPolicies") not in (None, [])
        or subnet.get("delegations") not in (None, [])
    ):
        _fail("target_subnet_route_present")
    if (
        az.run(
            "network",
            "vnet",
            "peering",
            "list",
            "--resource-group",
            group,
            "--vnet-name",
            names["vnet"],
        )
        != []
    ):
        _fail("target_vnet_peering_present")
    zone = target["private_dns_zone"]
    zone_result = az.run(
        "network",
        "private-dns",
        "zone",
        "create",
        "--resource-group",
        group,
        "--name",
        zone,
        "--tags",
        *tags,
    )
    zone_id = zone_result.get("id")
    if _lower_id(zone_id) != _lower_id(
        ctx.resource_id("Microsoft.Network/privateDnsZones", zone)
    ):
        _fail("private_dns_zone_mismatch")
    link = az.run(
        "network",
        "private-dns",
        "link",
        "vnet",
        "create",
        "--resource-group",
        group,
        "--zone-name",
        zone,
        "--name",
        names["link"],
        "--virtual-network",
        vnet_id,
        "--registration-enabled",
        "false",
        "--tags",
        *tags,
    )
    if (
        _lower_id(link.get("virtualNetwork", {}).get("id")) != _lower_id(vnet_id)
        or link.get("registrationEnabled") is not False
        or link.get("provisioningState") != "Succeeded"
    ):
        _fail("private_dns_link_mismatch")
    az.run(
        "network",
        "private-endpoint",
        "create",
        "--resource-group",
        group,
        "--name",
        names["pe"],
        "--location",
        ctx.policy["location"],
        "--vnet-name",
        names["vnet"],
        "--subnet",
        target["private_endpoint_subnet_name"],
        "--private-connection-resource-id",
        target_id,
        "--group-id",
        target["postgres_group_id"],
        "--connection-name",
        f"{names['base']}-connection",
        "--nic-name",
        names["nic"],
        "--tags",
        *tags,
    )
    expected_nic_id = _verify_private_endpoint(
        az, ctx, names, target_id, require_approved=True
    )
    zone_group = az.run(
        "network",
        "private-endpoint",
        "dns-zone-group",
        "create",
        "--resource-group",
        group,
        "--endpoint-name",
        names["pe"],
        "--name",
        "default",
        "--private-dns-zone",
        zone_id,
        "--zone-name",
        "postgres",
    )
    configs = zone_group.get("privateDnsZoneConfigs") or []
    if len(configs) != 1 or _lower_id(configs[0].get("privateDnsZoneId")) != _lower_id(
        zone_id
    ):
        _fail("private_dns_zone_group_mismatch")
    nic = az.run(
        "network", "nic", "show", "--resource-group", group, "--name", names["nic"]
    )
    ip_configs = nic.get("ipConfigurations")
    if ip_configs in (None, []) or any(
        item.get("publicIPAddress")
        or _lower_id(item.get("subnet", {}).get("id"))
        != _lower_id(_id(vnet_id, "subnets", target["private_endpoint_subnet_name"]))
        for item in ip_configs
    ):
        _fail("private_endpoint_nic_mismatch")
    if az.run("network", "public-ip", "list", "--resource-group", group) != []:
        _fail("target_public_ip_present")
    pe_id = ctx.resource_id("Microsoft.Network/privateEndpoints", names["pe"])
    return {
        "vnet_id": vnet_id,
        "subnet_id": _id(vnet_id, "subnets", target["private_endpoint_subnet_name"]),
        "private_endpoint_id": pe_id,
        "network_interface_id": expected_nic_id,
        "private_dns_zone_id": str(zone_id),
        "private_dns_link_id": _id(str(zone_id), "virtualNetworkLinks", names["link"]),
        "private_dns_zone_group_id": _id(pe_id, "privateDnsZoneGroups", "default"),
    }


def provision(az: Azure, ctx: Context, args: argparse.Namespace) -> dict[str, Any]:
    _assert_repo_context(ctx.policy, args)
    if not str(args.run_id).isdigit() or not str(args.run_attempt).isdigit():
        _fail("invalid_workflow_run_identity")
    _require_hex(args.source_sha, SHA_RE, "invalid_source_sha")
    _require_hex(args.candidate_sha, SHA_RE, "invalid_candidate_sha")
    _require_hex(args.inventory_digest, DIGEST_RE, "invalid_inventory_digest")
    _require_hex(args.quiescence_digest, DIGEST_RE, "invalid_quiescence_digest")
    if args.inventory_profile != "pre" or args.inventory_revision != "89c31f0ae275":
        _fail("source_inventory_profile_mismatch")
    if not isinstance(args.restore_time, str) or not UTC_RE.fullmatch(
        args.restore_time
    ):
        _fail("invalid_restore_time")
    try:
        restore_time = datetime.fromisoformat(
            args.restore_time.replace("Z", "+00:00")
        ).astimezone(UTC)
    except (ValueError, AttributeError):
        _fail("invalid_restore_time")
    ttl = args.ttl_hours
    if ttl < 1 or ttl > ctx.policy["target"]["maximum_ttl_hours"]:
        _fail("ttl_out_of_range")
    created = _now()
    expires = created + timedelta(hours=ttl)
    names = _names(ctx.policy, args.rehearsal_id, expires)
    _preflight_identity(az, ctx, "provision")
    _require_empty_group(az, ctx)
    _verify_source(az, ctx, restore_time)
    group = ctx.policy["target"]["resource_group"]
    tags = _tags(ctx, args.rehearsal_id, expires)
    az.run(
        "postgres",
        "flexible-server",
        "restore",
        "--resource-group",
        group,
        "--name",
        names["server"],
        "--source-server",
        ctx.source_server_id,
        "--restore-time",
        _iso(restore_time),
    )
    target_id = ctx.resource_id(
        "Microsoft.DBforPostgreSQL/flexibleServers", names["server"]
    )
    _verify_zero_firewalls(az, ctx, names["server"], locked=True)
    az.run(
        "postgres",
        "flexible-server",
        "update",
        "--resource-group",
        group,
        "--name",
        names["server"],
        "--public-access",
        "Disabled",
    )
    target_state = az.run("postgres", "flexible-server", "show", "--ids", target_id)
    if (
        _lower_id(target_state.get("id")) != _lower_id(target_id)
        or target_state.get("network", {}).get("publicNetworkAccess") != "Disabled"
    ):
        _fail("target_public_access_not_disabled")
    _verify_zero_firewalls(az, ctx, names["server"], locked=True)
    _tag(az, target_id, tags)
    network = _provision_network(az, ctx, names, tags, target_id)
    _verify_zero_firewalls(az, ctx, names["server"], locked=True)
    _verify_resource_set(az, ctx, names)
    return {
        "schema_version": 1,
        "policy_id": ctx.policy["policy_id"],
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "repository": args.repository,
        "workflow_sha": args.workflow_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "rehearsal_id": args.rehearsal_id,
        "source_inventory": {
            "artifact_sha256": args.inventory_digest,
            "quiescence_sha256": args.quiescence_digest,
            "profile": args.inventory_profile,
            "revision": args.inventory_revision,
            "source_sha": args.source_sha,
            "candidate_sha": args.candidate_sha,
        },
        "source_postgres": {
            "resource_id": ctx.source_server_id,
            "fqdn": ctx.policy["source"]["fqdn"],
            "database_name": ctx.policy["source"]["database_name"],
        },
        "target": {
            "postgres_resource_id": target_id,
            "database_name": ctx.policy["source"]["database_name"],
            **network,
        },
        "provisioner": {
            "client_id": ctx.policy["identities"]["provision"]["client_id"],
            "principal_id": ctx.policy["identities"]["provision"]["principal_id"],
        },
        "created_at": _iso(created),
        "expires_at": _iso(expires),
        "cleanup_not_after": _iso(expires),
        "isolation": {
            "public_access": "Disabled",
            "firewall_rule_count": 0,
            "vnet_peering_count": 0,
            "public_ip_count": 0,
            "unknown_resource_count": 0,
            "blob_copied": False,
            "redis_copied": False,
        },
        "pass": True,
    }


def _discover_sets(
    resources: list[dict[str, Any]], ctx: Context
) -> dict[str, tuple[dict[str, str], int]]:
    sets: dict[str, tuple[dict[str, str], int]] = {}
    zone_id = _lower_id(
        ctx.resource_id(
            "Microsoft.Network/privateDnsZones",
            ctx.policy["target"]["private_dns_zone"],
        )
    )
    zone_seen = False
    deferred: list[str] = []
    for resource in resources:
        rid = _lower_id(resource.get("id"))
        name = str(resource.get("name", "")).split("/")[-1]
        if rid == zone_id:
            zone_seen = True
            continue
        match = BASE_RE.fullmatch(name)
        if match is None:
            if name in {"default", "postgres"}:
                deferred.append(rid)
                continue
            _fail("unknown_or_malformed_resource")
        base, epoch, _ = match.groups()
        names = {
            "base": base,
            "server": f"{base}-pg",
            "vnet": f"{base}-vnet",
            "pe": f"{base}-pep",
            "nic": f"{base}-nic",
            "link": f"{base}-dnslink",
        }
        if rid not in _allowed_ids(ctx, names):
            _fail("unknown_or_malformed_resource")
        sets[base] = (names, int(epoch))
    if zone_seen and len(sets) != 1:
        _fail("orphan_shared_dns_zone")
    if len(sets) > 1:
        _fail("multiple_restore_resource_sets")
    allowed = set().union(*(_allowed_ids(ctx, names) for names, _ in sets.values()))
    if any(resource_id not in allowed for resource_id in deferred):
        _fail("unknown_or_malformed_resource")
    return sets


def _verify_cleanup_ownership(
    az: Azure,
    ctx: Context,
    resources: list[dict[str, Any]],
    names: dict[str, str],
    expiry: int,
) -> None:
    rehearsal_id = names["base"].split("-")[1]
    expected_tags = {
        **ctx.policy["target"]["required_tags"],
        "rehearsal-id": rehearsal_id,
        "expires-epoch": str(expiry),
    }
    nic_id = _lower_id(
        ctx.resource_id("Microsoft.Network/networkInterfaces", names["nic"])
    )
    pe_id = _lower_id(
        ctx.resource_id("Microsoft.Network/privateEndpoints", names["pe"])
    )
    actual = {_lower_id(item.get("id")) for item in resources}
    for item in resources:
        rid = _lower_id(item.get("id"))
        if (
            rid in _allowed_ids(ctx, names)
            and rid != nic_id
            and "/privatednszonegroups/" not in rid
        ):
            tags = item.get("tags") or {}
            if any(tags.get(key) != value for key, value in expected_tags.items()):
                _fail("cleanup_ownership_mismatch")
    if pe_id in actual:
        target_id = ctx.resource_id(
            "Microsoft.DBforPostgreSQL/flexibleServers", names["server"]
        )
        _verify_private_endpoint(az, ctx, names, target_id, require_approved=False)
    elif nic_id in actual:
        _fail("cleanup_ownership_mismatch")


def cleanup(az: Azure, ctx: Context, args: argparse.Namespace) -> dict[str, Any]:
    _assert_repo_context(ctx.policy, args)
    if not str(args.run_id).isdigit() or not str(args.run_attempt).isdigit():
        _fail("invalid_workflow_run_identity")
    if args.allow_missing and (args.action != "cleanup" or not args.force):
        _fail("invalid_allow_missing_mode")
    _preflight_identity(az, ctx, "cleanup")
    resources = _inventory(az, ctx)
    sets = _discover_sets(resources, ctx)
    if args.action == "cleanup" and not sets and not args.allow_missing:
        _fail("cleanup_target_not_found")
    now_epoch = int(_now().timestamp())
    selected: list[tuple[dict[str, str], int]] = []
    for names, expiry in sets.values():
        set_rehearsal = names["base"].split("-")[1]
        if args.action == "cleanup" and set_rehearsal != args.rehearsal_id:
            _fail("cleanup_rehearsal_mismatch")
        if not args.allow_missing:
            _verify_cleanup_ownership(az, ctx, resources, names, expiry)
        if args.action == "sweep" and expiry > now_epoch:
            continue
        if args.action == "cleanup" and not args.force and expiry > now_epoch:
            _fail("cleanup_before_expiry_requires_force")
        selected.append((names, expiry))
    deleted = 0
    for names, _ in selected:
        allowed = _allowed_ids(ctx, names)
        current = _inventory(az, ctx)
        actual = {_lower_id(item.get("id")) for item in current}
        if actual - allowed:
            _fail("unknown_target_resource")

        def priority(value: str) -> int:
            if "/privatednszonegroups/" in value:
                return 70
            if "/virtualnetworklinks/" in value:
                return 60
            if "/privateendpoints/" in value:
                return 50
            if "/networkinterfaces/" in value:
                return 40
            if "/flexibleservers/" in value:
                return 30
            if "/privatednszones/" in value:
                return 20
            return 10

        ordered = sorted(
            (item for item in actual if item in allowed), key=priority, reverse=True
        )
        for resource_id in ordered:
            live = {_lower_id(item.get("id")) for item in _inventory(az, ctx)}
            if live - allowed:
                _fail("unknown_target_resource")
            if resource_id not in live:
                continue
            az.run("resource", "delete", "--ids", resource_id, allow_empty=True)
            az.run(
                "resource",
                "wait",
                "--deleted",
                "--ids",
                resource_id,
                "--interval",
                "10",
                "--timeout",
                "900",
                allow_empty=True,
            )
            deleted += 1
        remaining = {_lower_id(item.get("id")) for item in _inventory(az, ctx)}
        if remaining & allowed:
            _fail("cleanup_incomplete")
    return {
        "schema_version": 1,
        "policy_id": ctx.policy["policy_id"],
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "repository": args.repository,
        "workflow_sha": args.workflow_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "action": args.action,
        "rehearsal_id": args.rehearsal_id if args.action == "cleanup" else None,
        "allow_missing": args.allow_missing,
        "resource_sets_found": len(sets),
        "resource_sets_deleted": len(selected),
        "resources_deleted": deleted,
        "unknown_resource_count": 0,
        "pass": True,
    }


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    payload = _canonical(receipt) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        checksum = f"{_sha256(payload)}  {path.name}\n".encode()
        sum_path = path.with_name("SHA256SUMS")
        descriptor = os.open(sum_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(checksum)
    except OSError:
        _fail("receipt_write_failed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=sorted(ALLOWED_ACTIONS))
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--triggering-actor", required=True)
    parser.add_argument("--intended-action", choices=["provision", "cleanup", "sweep"])
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--rehearsal-id")
    parser.add_argument("--source-sha")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--inventory-digest")
    parser.add_argument("--quiescence-digest")
    parser.add_argument("--inventory-profile", default="pre")
    parser.add_argument("--inventory-revision", default="89c31f0ae275")
    parser.add_argument("--restore-time")
    parser.add_argument("--ttl-hours", type=int, default=6)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    try:
        args = _parser().parse_args(argv)
        if args.policy.resolve() != POLICY_PATH.resolve():
            _fail("untrusted_policy_path")
        policy = _read_json(args.policy)
        _validate_policy(policy, executable=True)
        ctx = Context.from_policy(policy)
        _assert_repo_context(policy, args)
        if args.action == "validate-policy":
            if args.intended_action is None:
                _fail("missing_intended_action")
            print('{"action":"validate-policy","pass":true}')
            return 0
        if args.receipt is None:
            _fail("missing_receipt_path")
        if args.action == "provision":
            required = (
                args.rehearsal_id,
                args.source_sha,
                args.candidate_sha,
                args.inventory_digest,
                args.quiescence_digest,
                args.restore_time,
            )
            if any(value is None for value in required):
                _fail("missing_provision_argument")
            receipt = provision(Azure(), ctx, args)
        else:
            if args.action == "cleanup" and not args.rehearsal_id:
                _fail("missing_cleanup_rehearsal_id")
            receipt = cleanup(Azure(), ctx, args)
        _write_receipt(args.receipt, receipt)
        receipt_digest = _sha256(_canonical(receipt) + b"\n")
        print(
            json.dumps(
                {
                    "pass": True,
                    "action": args.action,
                    "receipt_sha256": f"sha256:{receipt_digest}",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except EnvelopeError as exc:
        if args is not None and args.receipt is not None:
            try:
                _write_receipt(
                    args.receipt,
                    {
                        "schema_version": 1,
                        "action": args.action,
                        "error": exc.code,
                        "pass": False,
                    },
                )
            except EnvelopeError:
                pass
        print(
            json.dumps(
                {"pass": False, "error": exc.code},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print('{"error":"internal_error","pass":false}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
