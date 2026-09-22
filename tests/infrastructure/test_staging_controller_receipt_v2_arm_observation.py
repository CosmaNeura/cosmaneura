from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import observe_staging_controller_receipt_v2_arm as arm
from scripts import read_staging_controller_receipt_v2_prefix as prefix_reader
from scripts import staging_restore_runtime_adapters as adapters
from scripts import validate_staging_controller_receipt_v2 as content
from scripts import validate_staging_controller_receipt_v2_production as production
from scripts import validate_staging_restore_launch_binding as launch

from tests.infrastructure import test_staging_controller_receipt_v2 as content_helpers
from tests.infrastructure import (
    test_staging_controller_receipt_v2_private_reader as ingress_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_production_authority as production_helpers,
)
from tests.infrastructure import test_staging_runtime_record_private_readers as wire

NOW = datetime(2026, 9, 5, 12, 10, tzinfo=UTC)
TOKEN = "arm-observation-token"
Failure = arm.ControllerReceiptV2ArmObservationFailure


def _permissions(*data_actions: str) -> list[dict[str, Any]]:
    return [
        dict(
            actions=[],
            notActions=[],
            dataActions=list(data_actions),
            notDataActions=[],
        )
    ]


READ_ACTION = "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"
CREATE_PERMISSIONS = _permissions(
    READ_ACTION,
    "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action",
)
READ_PERMISSIONS = _permissions(READ_ACTION)


@contextmanager
def _no_deadline(_seconds: int) -> Iterator[None]:
    yield


def _replace(value: Any, replacements: dict[str, str]) -> Any:
    if type(value) is str:
        return replacements.get(value, value)
    if type(value) is list:
        return [_replace(item, replacements) for item in value]
    if type(value) is dict:
        return {name: _replace(item, replacements) for name, item in value.items()}
    return value


def _permission_digest(permissions: list[dict[str, Any]]) -> str:
    keys = ("actions", "notActions", "dataActions", "notDataActions")
    normalized = [
        {key: sorted(item.get(key) or []) for key in keys} for item in permissions
    ]
    return content._digest(
        content._canonical(sorted(normalized, key=content._canonical))
    )


def _production_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    production.ControllerReceiptV2ProductionAuthority, dict[str, Any], dict[str, Any]
]:
    runtime, controller = ingress_helpers._configured_policies()
    runtime_env = runtime["runtime"]["container_apps_environment"]
    old_vnet = runtime_env["control_vnet_resource_id"]
    old_subnet = runtime["per_rehearsal_postgres"]["subnet_resource_id"]
    control_group = controller["fixed_bindings"]["executor_resource_group_id"]
    control_vnet = content_helpers._rid(
        control_group, "Microsoft.Network", "virtualNetworks", "restore-control"
    )
    executor_subnet = f"{control_vnet}/subnets/private-service-endpoints"
    infrastructure_subnet = f"{control_vnet}/subnets/executor-infrastructure"
    runtime_env["infrastructure_subnet_resource_id"] = infrastructure_subnet
    runtime = _replace(runtime, {old_vnet: control_vnet, old_subnet: executor_subnet})
    source = controller["record_chain_source"]
    subscription = controller["fixed_bindings"]["subscription_id"]
    role_root = f"/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions"
    container = source["container_resource_ids"]["controller_receipt"]
    assignment_root = f"{container}/providers/Microsoft.Authorization/roleAssignments"
    source.update(
        {
            "create_role_definition_id": f"{role_root}/10000000-0000-4000-8000-000000000001",
            "read_role_definition_id": f"{role_root}/10000000-0000-4000-8000-000000000002",
            "create_role_permissions_sha256": _permission_digest(CREATE_PERMISSIONS),
            "read_role_permissions_sha256": _permission_digest(READ_PERMISSIONS),
            "writer_role_assignment_id": f"{assignment_root}/20000000-0000-4000-8000-000000000001",
            "reader_role_assignment_ids": {
                "provision": f"{assignment_root}/20000000-0000-4000-8000-000000000002",
                "cleanup": f"{assignment_root}/20000000-0000-4000-8000-000000000003",
            },
        }
    )
    material, record = production_helpers._launch_material(
        monkeypatch, runtime, controller
    )
    records = ingress_helpers._records(
        runtime, controller, material.authenticated_receipt, record
    )
    identity = wire.Opener(
        [wire.Response(ingress_helpers._token(runtime, "provision"))], name="identity"
    )
    blob = ingress_helpers.Opener(
        [
            ingress_helpers.Response(records[kind])
            for kind in production.PREFIX_RECORD_KINDS
        ]
    )
    reader = prefix_reader.ManagedIdentityPrivateControllerReceiptV2PrefixReader._from_reviewed_configuration(
        runtime_policy=runtime,
        controller_policy=controller,
        production_policy=production.load_controller_receipt_v2_production_policy(),
        clock=lambda: NOW,
        identity_opener=identity,
        blob_opener=blob,
        epoch_clock=lambda: ingress_helpers.EPOCH_NOW,
        monotonic=lambda: 10.0,
        deadline_factory=_no_deadline,
    )
    monkeypatch.setenv("IDENTITY_ENDPOINT", wire.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", wire.IDENTITY_HEADER)
    monkeypatch.setenv(
        launch.EXECUTION_NAME_ENV, production_helpers.mailbox_helpers.EXECUTION_NAME
    )
    return reader.read_and_bind(launch_material_value=material), runtime, controller


def _base_document(request: arm._ArmRequest, resource_type: str) -> dict[str, Any]:
    return {
        "id": request.resource_id,
        "name": request.resource_id.rsplit("/", 1)[-1],
        "type": resource_type,
        "properties": {},
    }


def _documents(
    authority: production.ControllerReceiptV2ProductionAuthority,
) -> tuple[arm._ArmPlan, dict[str, Any], dict[str, dict[str, Any]]]:
    policy = arm.load_controller_receipt_v2_arm_observation_policy()
    plan, context = arm._derive_plan(authority, now=NOW, policy=policy)
    requests = {item.key: item for item in plan.requests}
    resources = context["resources"]
    documents: dict[str, dict[str, Any]] = {}
    types = {
        "target_postgres_server": "Microsoft.DBforPostgreSQL/flexibleServers",
        "target_vnet": "Microsoft.Network/virtualNetworks",
        "target_subnet": "Microsoft.Network/virtualNetworks/subnets",
        "target_private_endpoint": "Microsoft.Network/privateEndpoints",
        "target_network_interface": "Microsoft.Network/networkInterfaces",
        "target_private_dns_zone": "Microsoft.Network/privateDnsZones",
        "target_private_dns_link": "Microsoft.Network/privateDnsZones/virtualNetworkLinks",
        "target_private_dns_zone_group": "Microsoft.Network/privateEndpoints/privateDnsZoneGroups",
        "executor_control_vnet": "Microsoft.Network/virtualNetworks",
        "executor_subnet": "Microsoft.Network/virtualNetworks/subnets",
        "executor_private_endpoint": "Microsoft.Network/privateEndpoints",
        "executor_network_interface": "Microsoft.Network/networkInterfaces",
        "executor_private_dns_zone": "Microsoft.Network/privateDnsZones",
        "executor_private_dns_link": "Microsoft.Network/privateDnsZones/virtualNetworkLinks",
        "executor_private_dns_zone_group": "Microsoft.Network/privateEndpoints/privateDnsZoneGroups",
        "marker_storage_account": "Microsoft.Storage/storageAccounts",
        "controller_receipt_container": "Microsoft.Storage/storageAccounts/blobServices/containers",
        "controller_receipt_immutability_policy": "Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies",
        "marker_create_role_definition": "Microsoft.Authorization/roleDefinitions",
        "marker_read_role_definition": "Microsoft.Authorization/roleDefinitions",
        "marker_writer_role_assignment": "Microsoft.Authorization/roleAssignments",
        "marker_provision_reader_role_assignment": "Microsoft.Authorization/roleAssignments",
        "marker_cleanup_reader_role_assignment": "Microsoft.Authorization/roleAssignments",
    }
    documents.update(
        {name: _base_document(requests[name], kind) for name, kind in types.items()}
    )
    production_document = authority.authority
    intent = content._parse_canonical(
        authority.intent_bytes, content.MAXIMUM_POLICY_BYTES
    )
    documents["target_postgres_server"].update(
        {
            "location": production_document["request"]["location"],
            "tags": intent["required_tags"],
        }
    )
    documents["target_postgres_server"]["properties"] = {
        "state": "Ready",
        "fullyQualifiedDomainName": f"{production_document['request']['target_server_name']}.postgres.database.azure.com",
        "createMode": "PointInTimeRestore",
        "sourceServerResourceId": production_document["source"][
            "source_server_resource_id"
        ],
        "pointInTimeUTC": production_document["source"]["approved_restore_time"],
        "network": {"publicNetworkAccess": "Disabled"},
    }
    for side in ("target", "executor"):
        subnet = resources[f"{side}_subnet"]
        vnet_key = "target_vnet" if side == "target" else "executor_control_vnet"
        documents[vnet_key]["properties"] = {
            "addressSpace": {
                "addressPrefixes": [
                    "10.75.0.0/24" if side == "target" else "10.76.0.0/24"
                ]
            },
            "dhcpOptions": {"dnsServers": []},
            "subnets": [{"id": subnet}],
        }
        documents[f"{side}_subnet"]["properties"] = {
            "addressPrefix": "10.75.0.0/27" if side == "target" else "10.76.0.64/27",
            "privateEndpointNetworkPolicies": "Disabled",
            "defaultOutboundAccess": False,
            "routeTable": None,
            "natGateway": None,
            "networkSecurityGroup": None,
            "serviceEndpoints": [],
            "serviceEndpointPolicies": [],
            "delegations": [],
        }
        documents[f"{side}_private_endpoint"]["properties"] = {
            "subnet": {"id": subnet},
            "networkInterfaces": [{"id": resources[f"{side}_network_interface"]}],
            "privateLinkServiceConnections": [
                {
                    "privateLinkServiceId": resources["target_postgres_server"],
                    "groupIds": ["postgresqlServer"],
                    "privateLinkServiceConnectionState": {"status": "Approved"},
                }
            ],
        }
        private_ip = "10.75.0.5" if side == "target" else "10.76.0.70"
        config = dict(
            subnet={"id": subnet},
            privateIPAddress=private_ip,
            publicIPAddress=None,
        )
        nic = documents[f"{side}_network_interface"]
        nic["properties"] = {"ipConfigurations": [{"properties": config}]}
        documents[f"{side}_private_dns_zone"]["location"] = "global"
        documents[f"{side}_private_dns_link"]["properties"] = {
            "virtualNetwork": {"id": resources[vnet_key]},
            "registrationEnabled": False,
            "provisioningState": "Succeeded",
        }
        documents[f"{side}_private_dns_zone_group"]["properties"] = {
            "privateDnsZoneConfigs": [
                {
                    "properties": {
                        "privateDnsZoneId": resources[f"{side}_private_dns_zone"]
                    }
                }
            ]
        }
    documents["marker_storage_account"]["properties"] = {
        "publicNetworkAccess": "Disabled",
        "allowSharedKeyAccess": False,
        "allowBlobPublicAccess": False,
        "supportsHttpsTrafficOnly": True,
        "minimumTlsVersion": "TLS1_2",
        "defaultToOAuthAuthentication": True,
    }
    documents["controller_receipt_container"]["properties"] = {
        "publicAccess": "None",
        "hasImmutabilityPolicy": True,
        "hasLegalHold": True,
    }
    documents["controller_receipt_immutability_policy"]["properties"] = {
        "state": "Locked",
        "immutabilityPeriodSinceCreationInDays": 90,
        "allowProtectedAppendWrites": False,
        "allowProtectedAppendWritesAll": False,
    }
    documents["marker_create_role_definition"]["properties"] = {
        "permissions": CREATE_PERMISSIONS
    }
    documents["marker_read_role_definition"]["properties"] = {
        "permissions": READ_PERMISSIONS
    }
    source = context["controller"]["record_chain_source"]
    assignments = {
        "marker_writer_role_assignment": (
            production_document["identity"]["provision_job_uami_principal_id"],
            source["create_role_definition_id"],
        ),
        "marker_provision_reader_role_assignment": (
            production_document["identity"]["provision_job_uami_principal_id"],
            source["read_role_definition_id"],
        ),
        "marker_cleanup_reader_role_assignment": (
            context["runtime"]["identities"]["jobs"]["cleanup"]["principal_id"],
            source["read_role_definition_id"],
        ),
    }
    for key, (principal, role_definition) in assignments.items():
        documents[key]["properties"] = {
            "principalId": principal,
            "principalType": "ServicePrincipal",
            "roleDefinitionId": role_definition,
            "scope": resources["controller_receipt_container"],
            "condition": None,
            "delegatedManagedIdentityResourceId": None,
        }
    return plan, context, documents


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutate: Any = None,
    response_options: dict[str, Any] | None = None,
) -> tuple[Any, Any, Any, dict[str, Any]]:
    authority, runtime, _controller = _production_authority(monkeypatch)
    plan, _context, documents = _documents(authority)
    if mutate is not None:
        mutate(documents)
    identity = wire.Opener(
        [wire.Response(wire._token(runtime, adapters.ARM_RESOURCE, TOKEN))],
        name="identity",
    )
    responses = [
        wire.Response(
            content._canonical(documents[item.key]),
            **(response_options or {}),
        )
        for item in plan.requests
    ]
    provider = wire.Opener(responses, name="arm")
    observer = (
        arm.ManagedIdentityControllerReceiptV2ArmObserver._from_reviewed_configuration(
            clock=lambda: NOW,
            identity_opener=identity,
            arm_opener=provider,
            epoch_clock=lambda: wire.EPOCH_NOW,
            monotonic=lambda: 10.0,
            deadline_factory=_no_deadline,
        )
    )
    return observer, authority, identity, {"provider": provider, "plan": plan}


def test_exact_point_gets_mint_only_narrow_arm_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, authority, identity, case = _run(monkeypatch)
    result = observer.observe(production_authority=authority)
    document = result.authority
    assert len(identity.calls) == 1
    assert len(case["provider"].calls) == len(case["plan"].requests) == 23
    for (request, timeout), expected in zip(
        case["provider"].calls, case["plan"].requests, strict=True
    ):
        split = urlsplit(request.full_url)
        assert request.method == "GET"
        assert split.path == expected.resource_id
        assert parse_qs(split.query) == {"api-version": [expected.api_version]}
    assert (
        document["target"]["target_postgres_server_id"]
        == authority.authority["request"]["target_server_resource_id"]
    )
    assert (
        document["target"]["target_private_ip_address"],
        document["target"]["executor_private_ip_address"],
        document["target"]["executor_private_endpoint_subnet_cidr"],
    ) == ("10.75.0.5", "10.76.0.70", "10.76.0.64/27")
    configured = document["target"]["configured_executor_infrastructure_subnet"]
    assert configured == {
        "id": document["target"]["executor_control_vnet_id"]
        + "/subnets/executor-infrastructure",
        "cidr": "10.76.0.32/27",
    }
    for claim in (
        "blob_or_redis_copy_absence_proven",
        "controller_receipt_exists",
        "create_only_behavior_proven",
        "database_facts_authenticated",
        "provider_facts_authenticated",
        "runtime_authority",
        "writer_identity_or_private_route_proven",
        "executable",
    ):
        assert document["claims"][claim] is False
    assert "approved_database_role" not in document["target"]
    assert TOKEN not in repr(result)


def test_public_surface_has_no_selector_or_clock_and_remains_dormant() -> None:
    method = arm.ManagedIdentityControllerReceiptV2ArmObserver.observe
    assert set(inspect.signature(method).parameters) == {"self", "production_authority"}
    assert not inspect.signature(
        arm.build_controller_receipt_v2_arm_observer
    ).parameters
    with pytest.raises(Failure, match="policy_disabled"):
        arm.build_controller_receipt_v2_arm_observer()
    with pytest.raises(Failure, match="configuration_invalid"):
        arm.ManagedIdentityControllerReceiptV2ArmObserver()
    with pytest.raises(Failure, match="adapter_invalid"):
        arm._ExactArmPointGetAdapter()


def test_policy_is_exact_closed_world_and_all_activation_is_dark() -> None:
    policy = arm.load_controller_receipt_v2_arm_observation_policy()
    assert all(value is False for value in policy["activation"].values())
    assert policy["transport"]["maximum_token_requests"] == 1
    assert policy["transport"]["listing_permitted"] is False
    changed = deepcopy(policy)
    changed["transport"]["retry_permitted"] = True
    with pytest.raises(Failure, match="policy_invalid"):
        arm.validate_controller_receipt_v2_arm_observation_policy(changed)
    with pytest.raises(Failure, match="policy_disabled"):
        arm.validate_controller_receipt_v2_arm_observation_policy(
            policy, executable=True
        )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda docs: docs["target_postgres_server"]["properties"]["network"].update(
            publicNetworkAccess="Enabled"
        ),
        lambda docs: docs["target_private_endpoint"]["properties"][
            "privateLinkServiceConnections"
        ][0].update(groupIds=["wrong"]),
        lambda docs: docs["executor_network_interface"]["properties"][
            "ipConfigurations"
        ][0]["properties"].update(publicIPAddress={"id": "/bad"}),
        lambda docs: docs["target_network_interface"]["properties"]["ipConfigurations"][
            0
        ]["properties"].update(privateIPAddress="10.76.0.70"),
        lambda docs: docs["target_private_dns_link"]["properties"].update(
            registrationEnabled=True
        ),
        lambda docs: docs["controller_receipt_container"]["properties"].update(
            hasLegalHold=False
        ),
        lambda docs: docs["controller_receipt_immutability_policy"][
            "properties"
        ].update(state="Unlocked"),
        lambda docs: docs["marker_create_role_definition"]["properties"].update(
            permissions=[]
        ),
        lambda docs: docs["marker_writer_role_assignment"]["properties"].update(
            principalId="00000000-0000-4000-8000-000000000009"
        ),
    ),
)
def test_provider_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch, mutation: Any
) -> None:
    observer, authority, identity, case = _run(monkeypatch, mutate=mutation)
    with pytest.raises(Failure):
        observer.observe(production_authority=authority)
    assert len(identity.calls) == 1
    assert len(case["provider"].calls) == 23


@pytest.mark.parametrize(
    ("options", "failure"),
    [
        ({"status": 403}, "provider_refused"),
        ({"content_type": "text/plain"}, "provider_unavailable"),
        (
            {"url": "https://management.azure.com/wrong?api-version=2024-08-01"},
            "provider_unavailable",
        ),
    ],
)
def test_transport_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, Any],
    failure: str,
) -> None:
    observer, authority, _identity, _case_value = _run(
        monkeypatch, response_options=options
    )
    with pytest.raises(Failure, match=failure):
        observer.observe(production_authority=authority)


def test_wrong_identity_and_unsealed_authority_refuse_before_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, authority, identity, case = _run(monkeypatch)
    token = json.loads(identity.responses[0].body)
    token["client_id"] = "00000000-0000-4000-8000-000000000009"
    identity.responses[0] = wire.Response(content._canonical(token))
    with pytest.raises(Failure, match="identity_failed"):
        observer.observe(production_authority=authority)
    observer, authority, _identity, case = _run(monkeypatch)
    with pytest.raises(Failure, match="production_authority_invalid"):
        arm._derive_plan(
            authority,
            now=NOW - timedelta(seconds=1),
            policy=arm.load_controller_receipt_v2_arm_observation_policy(),
        )
    object.__setattr__(authority, "execution_binding", "changed")
    with pytest.raises(Failure, match="production_authority_invalid"):
        observer.observe(production_authority=authority)
    assert case["provider"].calls == []


def test_one_shot_and_seal_revalidate_exact_raw_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, authority, _identity, _case_value = _run(monkeypatch)
    result = observer.observe(production_authority=authority)
    with pytest.raises(Failure, match="already_used"):
        observer.observe(production_authority=authority)
    object.__setattr__(result._responses[0], "body", b"{}")
    assert not result._is_authenticated()
    with pytest.raises(Failure, match="authority_invalid"):
        _ = result.authority


def test_no_live_or_mutating_import_path() -> None:
    source = inspect.getsource(arm)
    forbidden = 'method="POST"|method="PUT"|method="DELETE"|subprocess|open("|requests.'
    for token in forbidden.split("|"):
        assert token not in source
