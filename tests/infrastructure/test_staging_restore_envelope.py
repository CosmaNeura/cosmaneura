from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/staging_restore_envelope.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-envelope-policy.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("staging_restore_envelope", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


envelope = _load_module()


def _configured_policy() -> dict[str, Any]:
    policy = json.loads(POLICY.read_text())
    policy["trusted_evidence_binding_implemented"] = True
    policy["tenant_id"] = "11111111-1111-4111-8111-111111111111"
    policy["subscription_id"] = "22222222-2222-4222-8222-222222222222"
    role_root = "/subscriptions/22222222-2222-4222-8222-222222222222/providers/Microsoft.Authorization/roleDefinitions/"
    policy["deny_policy"]["definition_id"] = role_root.replace("roleDefinitions/", "policyDefinitions/deny-firewall")  # fmt: skip
    rule = {"if": {"field": "type", "equals": "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules"}, "then": {"effect": "deny"}}  # fmt: skip
    policy["deny_policy"]["canonical_rule_sha256"] = envelope._sha256(envelope._canonical(rule))  # fmt: skip
    # fmt: off
    policy["identities"]["provision"].update(
        client_id="33333333-3333-4333-8333-333333333333",
        principal_id="44444444-4444-4444-8444-444444444444",
        required_target_role_definition_ids=[role_root + "70000000-0000-4000-8000-000000000001"],
        required_source_role_definition_ids=[role_root + "70000000-0000-4000-8000-000000000002"],
        required_control_role_definition_ids=[role_root + "70000000-0000-4000-8000-000000000003"],
    )
    policy["identities"]["cleanup"].update(
        client_id="55555555-5555-4555-8555-555555555555",
        principal_id="66666666-6666-4666-8666-666666666666",
        required_target_role_definition_ids=[role_root + "70000000-0000-4000-8000-000000000004"],
        required_control_role_definition_ids=[role_root + "70000000-0000-4000-8000-000000000005"],
    )
    # fmt: on
    digest = envelope._role_permissions_digest({"permissions": ROLE_PERMISSIONS})
    for identity in policy["identities"].values():
        for kind in ("target", "source", "control"):
            ids = identity.get(f"required_{kind}_role_definition_ids", [])
            identity[f"required_{kind}_role_permissions_sha256"] = [digest] * len(ids)
    return policy


ROLE_PERMISSIONS = [{"actions": ["Microsoft.Authorization/roleAssignments/read"], "notActions": [], "dataActions": [], "notDataActions": []}]  # fmt: skip


def _args(**changes: Any) -> SimpleNamespace:
    # fmt: off
    values = {
        "action": "provision", "repository": "CosmaNeura/cosmaneura",
        "ref": "refs/heads/master", "workflow_sha": "a" * 40,
        "run_id": "901", "run_attempt": "1", "event_name": "workflow_dispatch",
        "actor": "JohnClem", "triggering_actor": "JohnClem", "intended_action": None,
        "rehearsal_id": "012345abcdef", "source_sha": "b" * 40, "candidate_sha": "c" * 40,
        "inventory_digest": "sha256:" + "d" * 64, "quiescence_digest": "sha256:" + "e" * 64,
        "inventory_profile": "pre", "inventory_revision": "89c31f0ae275",
        "restore_time": "2026-09-02T12:00:00Z", "ttl_hours": 6,
        "force": False, "allow_missing": False,
    }
    # fmt: on
    values.update(changes)
    return SimpleNamespace(**values)


class FakeAzure:
    def __init__(self, policy: dict[str, Any], *, identity_kind: str = "provision") -> None:  # fmt: skip
        self.policy = policy
        self.ctx = envelope.Context.from_policy(policy)
        self.calls: list[tuple[str, ...]] = []
        self.resources: list[dict[str, Any]] = []
        self.target_public_access = "Disabled"
        self.connection_status = "Approved"
        self.identity_kind = identity_kind
        self.account_subscription_id: str | None = None
        self.sp_principal_id: str | None = None
        self.target_group_id: str | None = None
        self.private_link_service_id: str | None = None
        self.pe_subnet_id: str | None = None
        self.nic_subnet_id: str | None = None
        self.assignment_overrides: list[object] = []
        self.resource_selectors: list[object] = []
        self.extra_federated_credential = False
        self.definition_mode = "All"
        self.role_permissions = deepcopy(ROLE_PERMISSIONS)
        self.target_firewalls: list[dict[str, Any]] = []
        self.firewall_next_link: str | None = None
        self.vnet_dns_servers: list[str] = []
        self.vnet_extra_subnet = False
        self.subnet_overrides: dict[str, Any] = {}

    def run(self, *args: str, allow_empty: bool = False) -> Any:
        del allow_empty
        self.calls.append(args)
        key = args[:3]
        cleanup = self.policy["identities"]["cleanup"]
        if args[:2] == ("account", "show"):
            kind = self.policy["identities"][self.identity_kind]
            return {
                "tenantId": self.policy["tenant_id"],
                "id": self.account_subscription_id or self.policy["subscription_id"],
                "user": {"type": "servicePrincipal", "name": kind["client_id"]},
            }
        if args[:2] == ("group", "show"):
            return {"id": self.target_group_id or self.ctx.target_group_id, "location": "northcentralus"}  # fmt: skip
        if args[:3] == ("ad", "sp", "show"):
            identity = self.policy["identities"][self.identity_kind]
            return {
                "appId": identity["client_id"],
                "id": self.sp_principal_id or identity["principal_id"],
            }
        if args[:3] == ("role", "assignment", "list"):
            identity = self.policy["identities"][self.identity_kind]
            principal = identity["principal_id"]
            scope = args[args.index("--scope") + 1]
            scoped = [(envelope._id("subscriptions", self.ctx.subscription), "required_control_role_definition_ids")]  # fmt: skip
            if scope == self.ctx.target_group_id:
                scoped.append((scope, "required_target_role_definition_ids"))
            elif scope == self.ctx.source_server_id:
                scoped.append((self.ctx.source_server_id, "required_source_role_definition_ids"))  # fmt: skip
            return [
                {"principalId": principal, "scope": scope, "roleDefinitionId": role}
                for scope, key_name in scoped
                for role in identity[key_name]
            ]
        if args[:3] == ("role", "definition", "show"):
            role_id = args[args.index("--id") + 1]
            return {"id": role_id, "permissions": self.role_permissions}
        if key == ("ad", "app", "federated-credential"):
            client = args[args.index("--id") + 1]
            kind = "cleanup" if client == cleanup["client_id"] else "provision"
            credentials = [
                {
                    "issuer": self.policy["federation"]["issuer"],
                    "subject": self.policy["federation"][f"{kind}_subject"],
                    "audiences": self.policy["federation"]["audiences"],
                }
            ]
            if self.extra_federated_credential:
                credentials.append({"issuer": "https://example.invalid"})
            return credentials
        if key == ("policy", "assignment", "show"):
            return {
                "scope": self.ctx.target_group_id,
                "policyDefinitionId": self.policy["deny_policy"]["definition_id"],
                "enforcementMode": "Default",
                "overrides": self.assignment_overrides,
                "resourceSelectors": self.resource_selectors,
            }
        if key == ("policy", "definition", "show"):
            return {
                "mode": self.definition_mode,
                "policyRule": {
                    "if": {
                        "field": "type",
                        "equals": "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules",
                    },
                    "then": {"effect": "deny"},
                },
            }
        if key == ("policy", "exemption", "list"):
            return []
        if key == ("resource", "list", "--resource-group"):
            return deepcopy(self.resources)
        if key == ("postgres", "flexible-server", "show"):
            resource_id = args[args.index("--ids") + 1]
            if resource_id == self.ctx.source_server_id:
                return {
                    "id": resource_id,
                    "name": self.policy["source"]["server_name"],
                    "fullyQualifiedDomainName": self.policy["source"]["fqdn"],
                    "location": "northcentralus",
                    "version": "16",
                    "network": {"publicNetworkAccess": "Enabled"},
                    "backup": {
                        "backupRetentionDays": 7,
                        "earliestRestoreDate": "2026-09-01T00:00:00Z",
                    },
                }
            return {
                "id": resource_id,
                "network": {"publicNetworkAccess": self.target_public_access},
            }
        if key == ("postgres", "flexible-server", "restore"):
            return {}
        if key == ("postgres", "flexible-server", "firewall-rule"):
            group = args[args.index("--resource-group") + 1]
            if group == self.policy["source"]["resource_group"]:
                return [
                    {
                        "name": name,
                        "startIpAddress": start,
                        "endIpAddress": end,
                    }
                    for name, start, end in self.policy["source"]["firewall_rules"]
                ]
            return []
        if key == ("postgres", "flexible-server", "update"):
            return {}
        if args[:1] == ("rest",):
            return {"value": self.target_firewalls, "nextLink": self.firewall_next_link}
        if key == ("resource", "tag", "--ids"):
            return {}
        if key == ("network", "vnet", "create"):
            return {}
        if key == ("network", "vnet", "show"):
            subnets = [{"name": "private-endpoints"}]
            if self.vnet_extra_subnet:
                subnets.append({"name": "unexpected"})
            return {
                "addressSpace": {"addressPrefixes": ["10.254.0.0/24"]},
                "dhcpOptions": {"dnsServers": self.vnet_dns_servers},
                "subnets": subnets,
            }
        if args[:4] == ("network", "vnet", "subnet", "update"):
            return {}
        if args[:4] == ("network", "vnet", "subnet", "show"):
            subnet = {
                "addressPrefix": "10.254.0.0/27",
                "privateEndpointNetworkPolicies": "Disabled",
                "defaultOutboundAccess": False,
                "routeTable": None,
                "natGateway": None,
                "networkSecurityGroup": None,
                "serviceEndpoints": [],
                "serviceEndpointPolicies": [],
                "delegations": [],
            }
            subnet.update(self.subnet_overrides)
            return subnet
        if args[:4] == ("network", "vnet", "peering", "list"):
            return []
        if args[:4] == ("network", "private-dns", "zone", "create"):
            zone = self.policy["target"]["private_dns_zone"]
            return {
                "id": self.ctx.resource_id("Microsoft.Network/privateDnsZones", zone)
            }
        if args[:4] == ("network", "private-dns", "link", "vnet"):
            link_name = args[args.index("--name") + 1]
            return {
                "virtualNetwork": {
                    "id": self.ctx.resource_id(
                        "Microsoft.Network/virtualNetworks",
                        link_name.replace("-dnslink", "-vnet"),
                    )
                },
                "registrationEnabled": False,
                "provisioningState": "Succeeded",
            }
        if key == ("network", "private-endpoint", "create"):
            return {}
        # fmt: off
        if key == ("network", "private-endpoint", "show"):
            pe_name = args[args.index("--name") + 1]
            vnet_id = self.ctx.resource_id("Microsoft.Network/virtualNetworks", pe_name.replace("-pep", "-vnet"))
            target_id = self.ctx.resource_id("Microsoft.DBforPostgreSQL/flexibleServers", pe_name.replace("-pep", "-pg"))
            nic_id = self.ctx.resource_id("Microsoft.Network/networkInterfaces", pe_name.replace("-pep", "-nic"))
            return {
                "subnet": {"id": self.pe_subnet_id or vnet_id + "/subnets/private-endpoints"},
                "privateLinkServiceConnections": [{"groupIds": ["postgresqlServer"], "privateLinkServiceId": self.private_link_service_id or target_id, "privateLinkServiceConnectionState": {"status": self.connection_status}}],
                "networkInterfaces": [{"id": nic_id}],
            }
        if args[:4] == ("network", "private-endpoint", "dns-zone-group", "create"):
            return {
                "privateDnsZoneConfigs": [
                    {
                        "privateDnsZoneId": self.ctx.resource_id(
                            "Microsoft.Network/privateDnsZones",
                            self.policy["target"]["private_dns_zone"],
                        )
                    }
                ]
            }
        if key == ("network", "nic", "show"):
            nic_name = args[args.index("--name") + 1]
            vnet_id = self.ctx.resource_id("Microsoft.Network/virtualNetworks", nic_name.replace("-nic", "-vnet"))
            return {"ipConfigurations": [{"privateIPAddress": "10.254.0.4", "publicIPAddress": None, "subnet": {"id": self.nic_subnet_id or vnet_id + "/subnets/private-endpoints"}}]}
        # fmt: on
        if key == ("network", "public-ip", "list"):
            return []
        if key == ("resource", "delete", "--ids"):
            rid = envelope._lower_id(args[args.index("--ids") + 1])
            self.resources = [
                item for item in self.resources if envelope._lower_id(item["id"]) != rid
            ]
            return None
        if key == ("resource", "wait", "--deleted"):
            return None
        raise AssertionError(f"unhandled fake Azure command: {args}")


def _set_complete_resources(
    fake: FakeAzure, rehearsal_id: str, expires: datetime
) -> dict[str, str]:
    names = envelope._names(fake.policy, rehearsal_id, expires)
    tags = dict(fake.policy["target"]["required_tags"])
    tags.update(
        {"rehearsal-id": rehearsal_id, "expires-epoch": str(int(expires.timestamp()))}
    )
    types = {
        "server": "Microsoft.DBforPostgreSQL/flexibleServers",
        "vnet": "Microsoft.Network/virtualNetworks",
        "pe": "Microsoft.Network/privateEndpoints",
        "nic": "Microsoft.Network/networkInterfaces",
    }
    fake.resources = [
        {"id": fake.ctx.resource_id(kind, names[key]), "name": names[key], "tags": tags}
        for key, kind in types.items()
    ]
    zone = fake.policy["target"]["private_dns_zone"]
    fake.resources.append(
        {
            "id": fake.ctx.resource_id("Microsoft.Network/privateDnsZones", zone),
            "name": zone,
            "tags": tags,
        }
    )
    return names


def test_policy_is_pinned_closed_world_and_unconfigured() -> None:
    policy = json.loads(POLICY.read_text())
    assert envelope._sha256(POLICY.read_bytes()) == envelope.POLICY_SHA256
    envelope._validate_policy(policy, executable=False)
    with pytest.raises(
        envelope.EnvelopeError, match="policy_placeholders_unconfigured"
    ):
        envelope._validate_policy(policy, executable=True)
    assert policy["source"]["server_name"] == "cosmaneura-staging-postgres"
    assert policy["source"]["firewall_rules"][1] == [
        "allow-access-from-azure-services",
        "0.0.0.0",
        "0.0.0.0",
    ]
    assert policy["target"]["resource_group"] == "cosmaneura-staging-rehearsal"
    assert policy["target"]["maximum_ttl_hours"] == 24
    assert policy["trusted_evidence_binding_implemented"] is False
    assert policy["github_environment_controls"]["protected_branches_only"] is True
    envelope._validate_policy(_configured_policy(), executable=True)


def test_role_preflight_requires_exact_assignment_set() -> None:
    policy = _configured_policy()
    required = policy["identities"]["provision"]["required_target_role_definition_ids"]
    actual = [
        {
            "principalId": policy["identities"]["provision"]["principal_id"],
            "scope": envelope.Context.from_policy(policy).target_group_id,
            "roleDefinitionId": required[0],
        }
    ]
    expected = [(actual[0]["scope"], required[0])]
    envelope._require_roles(actual, expected, actual[0]["principalId"])
    actual.append({**actual[0], "principalId": "88888888-8888-4888-8888-888888888888"})
    with pytest.raises(envelope.EnvelopeError, match="role_assignment_mismatch"):
        envelope._require_roles(
            actual, expected, policy["identities"]["provision"]["principal_id"]
        )
    actual.pop()
    actual.append(
        {
            **actual[0],
            "roleDefinitionId": "/providers/Microsoft.Authorization/roleDefinitions/owner",
        }
    )
    with pytest.raises(envelope.EnvelopeError, match="role_assignment_mismatch"):
        envelope._require_roles(actual, expected, actual[0]["principalId"])


def test_deny_policy_and_federation_are_verified_before_mutation() -> None:
    policy = _configured_policy()
    fake = FakeAzure(policy)
    envelope._preflight_identity(fake, fake.ctx, "provision")
    calls = [" ".join(item) for item in fake.calls]
    assert all(
        "--include-inherited --include-groups" in item
        for item in calls
        if "role assignment list" in item
    )
    assert any("federated-credential list" in item for item in calls)
    assert any("policy assignment show" in item for item in calls)
    assert any("policy definition show" in item for item in calls)
    mismatch = FakeAzure(policy)
    mismatch.sp_principal_id = "77777777-7777-4777-8777-777777777777"
    with pytest.raises(
        envelope.EnvelopeError, match="azure_service_principal_mismatch"
    ):
        envelope._preflight_identity(mismatch, mismatch.ctx, "provision")
    mismatch = FakeAzure(policy)
    mismatch.extra_federated_credential = True
    with pytest.raises(envelope.EnvelopeError, match="federated_credential_mismatch"):
        envelope._preflight_identity(mismatch, mismatch.ctx, "provision")
    for field in ("assignment_overrides", "resource_selectors"):
        unsafe = FakeAzure(policy)
        setattr(unsafe, field, [{"kind": "unsafe"}])
        with pytest.raises(
            envelope.EnvelopeError, match="deny_policy_assignment_mismatch"
        ):
            envelope._preflight_identity(unsafe, unsafe.ctx, "provision")
    unsafe = FakeAzure(policy)
    unsafe.definition_mode = "Indexed"
    with pytest.raises(envelope.EnvelopeError, match="deny_policy_rule_mismatch"):
        envelope._preflight_identity(unsafe, unsafe.ctx, "provision")
    unsafe = FakeAzure(policy)
    unsafe.role_permissions[0]["actions"].append(
        "Microsoft.Network/publicIPAddresses/write"
    )
    with pytest.raises(envelope.EnvelopeError, match="role_definition_mismatch"):
        envelope._preflight_identity(unsafe, unsafe.ctx, "provision")
    policy["deny_policy"]["canonical_rule_sha256"] = "0" * 64
    with pytest.raises(envelope.EnvelopeError, match="deny_policy_rule_mismatch"):
        envelope._preflight_identity(
            FakeAzure(policy), envelope.Context.from_policy(policy), "provision"
        )


def test_provision_locks_down_before_private_network_and_emits_aggregate_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _configured_policy()
    fake = FakeAzure(policy)
    created = datetime(2026, 9, 3, 12, tzinfo=UTC)
    expires = created + timedelta(hours=6)
    _set_complete_resources(fake, "012345abcdef", expires)
    final_resources = deepcopy(fake.resources)
    fake.resources = []
    original_run = fake.run

    def staged_run(*arguments: str, allow_empty: bool = False) -> Any:
        result = original_run(*arguments, allow_empty=allow_empty)
        if arguments[:3] == ("network", "public-ip", "list"):
            fake.resources = deepcopy(final_resources)
        return result

    fake.run = staged_run  # type: ignore[method-assign]
    monkeypatch.setattr(envelope, "_now", lambda: created)
    receipt = envelope.provision(fake, fake.ctx, _args())
    calls = [item[:3] for item in fake.calls]
    first_firewall = calls.index(("postgres", "flexible-server", "firewall-rule"))
    lockdown = calls.index(("postgres", "flexible-server", "update"))
    vnet = calls.index(("network", "vnet", "create"))
    assert first_firewall < lockdown < vnet
    joined = "\n".join(" ".join(item) for item in fake.calls)
    assert joined.count("rest --method get --url") == 3
    assert "--server-name cosmaneura-staging-postgres" in joined
    assert "user create" not in joined and "database create" not in joined
    assert " storage " not in joined and " redis " not in joined
    assert "private-dns zone create" in joined and "dns-zone-group create" in joined
    assert receipt["isolation"] == {
        "public_access": "Disabled",
        "firewall_rule_count": 0,
        "vnet_peering_count": 0,
        "public_ip_count": 0,
        "unknown_resource_count": 0,
        "blob_copied": False,
        "redis_copied": False,
    }
    assert receipt["source_inventory"]["profile"] == "pre"
    assert receipt["source_inventory"]["revision"] == "89c31f0ae275"
    assert receipt["cleanup_not_after"] == "2026-09-03T18:00:00Z"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (dict(ttl_hours=25), "ttl_out_of_range"),
        (dict(inventory_profile="post"), "source_inventory_profile_mismatch"),
        (dict(ref="refs/heads/feature"), "unprotected_repository_context"),
        (dict(actor="someone-else"), "founder_authorization_required"),
        (dict(triggering_actor="someone-else"), "founder_authorization_required"),
        (dict(run_id="not-a-run"), "invalid_workflow_run_identity"),
    ],
)
def test_provision_rejects_invalid_authority_before_azure(
    change: dict[str, Any], code: str
) -> None:
    policy = _configured_policy()
    fake = FakeAzure(policy)
    with pytest.raises(envelope.EnvelopeError, match=code):
        envelope.provision(fake, fake.ctx, _args(**change))
    assert fake.calls == []


def test_provision_rejects_inherited_firewall_and_failed_lockdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _configured_policy()
    fake = FakeAzure(policy)
    monkeypatch.setattr(envelope, "_now", lambda: datetime(2026, 9, 3, 12, tzinfo=UTC))
    fake.target_firewalls = [{"name": "inherited"}]
    with pytest.raises(envelope.EnvelopeError, match="target_firewall_rule_present"):
        envelope.provision(fake, fake.ctx, _args())
    assert not any(call[:3] == ("network", "vnet", "create") for call in fake.calls)
    fake = FakeAzure(policy)
    fake.firewall_next_link = "https://management.azure.com/next"
    with pytest.raises(
        envelope.EnvelopeError, match="target_firewall_inventory_incomplete"
    ):
        envelope.provision(fake, fake.ctx, _args())
    fake = FakeAzure(policy)
    fake.target_public_access = "Enabled"
    with pytest.raises(
        envelope.EnvelopeError, match="target_public_access_not_disabled"
    ):
        envelope.provision(fake, fake.ctx, _args())


def test_provision_remains_disabled_without_authenticated_evidence_binding() -> None:
    policy = _configured_policy()
    policy["trusted_evidence_binding_implemented"] = False
    fake = FakeAzure(policy)
    with pytest.raises(
        envelope.EnvelopeError, match="trusted_evidence_binding_not_implemented"
    ):
        envelope.provision(fake, fake.ctx, _args())
    assert fake.calls == []


@pytest.mark.parametrize(("attribute", "value"), [("vnet_dns_servers", ["10.0.0.4"]), ("vnet_extra_subnet", True), ("subnet_overrides", {"routeTable": {"id": "unexpected"}}), ("subnet_overrides", {"natGateway": {"id": "unexpected"}}), ("subnet_overrides", {"networkSecurityGroup": {"id": "unexpected"}}), ("subnet_overrides", {"serviceEndpoints": [{"service": "Storage"}]}), ("subnet_overrides", {"serviceEndpointPolicies": [{"id": "unexpected"}]}), ("subnet_overrides", {"delegations": [{"name": "unexpected"}]}), ("pe_subnet_id", "/unexpected"), ("nic_subnet_id", "/unexpected")])  # fmt: skip
def test_provision_rejects_additional_network_paths(monkeypatch: pytest.MonkeyPatch, attribute: str, value: Any) -> None:  # fmt: skip
    policy = _configured_policy()
    fake = FakeAzure(policy)
    setattr(fake, attribute, value)
    monkeypatch.setattr(envelope, "_now", lambda: datetime(2026, 9, 3, 12, tzinfo=UTC))
    with pytest.raises(envelope.EnvelopeError):
        envelope.provision(fake, fake.ctx, _args())


def test_cleanup_refuses_unknown_or_malformed_resource_without_delete() -> None:
    policy = _configured_policy()
    fake = FakeAzure(policy, identity_kind="cleanup")
    fake.resources = [
        {
            "id": fake.ctx.resource_id(
                "Microsoft.Network/networkSecurityGroups",
                "privatelink.postgres.database.azure.com/unexpected",
            ),
            "name": "unexpected",
            "tags": {},
        }
    ]
    with pytest.raises(envelope.EnvelopeError, match="unknown_or_malformed_resource"):
        envelope.cleanup(fake, fake.ctx, _args(action="sweep", rehearsal_id=None))
    assert not any(call[:3] == ("resource", "delete", "--ids") for call in fake.calls)

    tagged = FakeAzure(policy, identity_kind="cleanup")
    _set_complete_resources(
        tagged, "012345abcdef", datetime(2026, 9, 3, 11, tzinfo=UTC)
    )
    tagged.resources[0]["tags"]["managed-by"] = "someone-else"
    with pytest.raises(envelope.EnvelopeError, match="cleanup_ownership_mismatch"):
        envelope.cleanup(tagged, tagged.ctx, _args(action="sweep", rehearsal_id=None))
    assert not any(call[:3] == ("resource", "delete", "--ids") for call in tagged.calls)

    bound = FakeAzure(policy, identity_kind="cleanup")
    _set_complete_resources(bound, "012345abcdef", datetime(2026, 9, 3, 11, tzinfo=UTC))
    bound.private_link_service_id = "/unexpected"
    with pytest.raises(envelope.EnvelopeError, match="private_endpoint_connection_mismatch"):  # fmt: skip
        envelope.cleanup(bound, bound.ctx, _args(action="sweep", rehearsal_id=None))


@pytest.mark.parametrize(("attribute", "code"), [("account_subscription_id", "azure_identity_mismatch"), ("sp_principal_id", "azure_service_principal_mismatch"), ("target_group_id", "target_resource_group_mismatch")])  # fmt: skip
def test_cleanup_requires_exact_identity_before_delete(attribute: str, code: str) -> None:  # fmt: skip
    policy = _configured_policy()
    fake = FakeAzure(policy, identity_kind="cleanup")
    setattr(fake, attribute, "unexpected")
    with pytest.raises(envelope.EnvelopeError, match=code):
        envelope.cleanup(fake, fake.ctx, _args(action="sweep", rehearsal_id=None))
    assert not any(call[:3] == ("resource", "delete", "--ids") for call in fake.calls)


def test_expired_sweep_deletes_exact_set_and_future_sweep_is_dormant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _configured_policy()
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    monkeypatch.setattr(envelope, "_now", lambda: now)
    expired = FakeAzure(policy, identity_kind="cleanup")
    _set_complete_resources(expired, "012345abcdef", now - timedelta(minutes=1))
    receipt = envelope.cleanup(
        expired, expired.ctx, _args(action="sweep", rehearsal_id=None)
    )
    assert receipt["resource_sets_deleted"] == 1
    assert expired.resources == []
    assert not any(call[:2] == ("policy", "assignment") for call in expired.calls)
    future = FakeAzure(policy, identity_kind="cleanup")
    _set_complete_resources(future, "012345abcdef", now + timedelta(hours=1))
    receipt = envelope.cleanup(
        future, future.ctx, _args(action="sweep", rehearsal_id=None)
    )
    assert receipt["resource_sets_deleted"] == 0
    assert not any(call[:3] == ("resource", "delete", "--ids") for call in future.calls)


def test_failed_run_cleanup_requires_exact_id_but_force_ignores_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _configured_policy()
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    monkeypatch.setattr(envelope, "_now", lambda: now)
    fake = FakeAzure(policy, identity_kind="cleanup")
    _set_complete_resources(fake, "012345abcdef", now + timedelta(hours=6))
    with pytest.raises(envelope.EnvelopeError, match="cleanup_rehearsal_mismatch"):
        envelope.cleanup(
            fake,
            fake.ctx,
            _args(action="cleanup", rehearsal_id="ffffffffffff", force=True),
        )
    receipt = envelope.cleanup(fake, fake.ctx, _args(action="cleanup", force=True))
    assert receipt["resource_sets_deleted"] == 1

    missing = FakeAzure(policy, identity_kind="cleanup")
    with pytest.raises(envelope.EnvelopeError, match="cleanup_target_not_found"):
        envelope.cleanup(missing, missing.ctx, _args(action="cleanup", force=True))
    receipt = envelope.cleanup(
        missing,
        missing.ctx,
        _args(action="cleanup", force=True, allow_missing=True),
    )
    assert receipt["resource_sets_deleted"] == 0


def test_azure_failure_never_echoes_provider_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "postgresql://operator:secret@example.invalid/staging"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=secret),
    )
    with pytest.raises(envelope.EnvelopeError) as error:
        envelope.Azure().run("account", "show")
    assert error.value.code == "azure_operation_failed"
    assert secret not in str(error.value)
