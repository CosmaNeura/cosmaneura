#!/usr/bin/env python3
"""Dormant exact-point ARM observation for controller-receipt-v2.

The public data plane accepts only a sealed production authority.  The result
authenticates selected ARM response facts, not database state, routing, resource
absence, creator identity, controller-receipt existence, or runtime authority.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import read_staging_controller_receipt_v2 as ingress
    from scripts import staging_restore_runtime_adapters as adapters
    from scripts import validate_staging_controller_receipt_v2 as content
    from scripts import validate_staging_controller_receipt_v2_production as production
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import read_staging_controller_receipt_v2 as ingress
    import staging_restore_runtime_adapters as adapters
    import validate_staging_controller_receipt_v2 as content
    import validate_staging_controller_receipt_v2_production as production
    import validate_staging_restore_runtime_binding as runtime_binding


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/controller-receipt-v2-arm-observation-policy.json"
)
POLICY_SHA256 = "1e343aca763397868a57a95ab91b33e51d0ab577b1b8a45447994d058f4b3061"
MAXIMUM_POLICY_BYTES = 64 * 1024
PLACEHOLDER_RE = re.compile(r"^CONFIGURE_EXACT_[A-Z0-9_]+$")
TARGET_RE = re.compile(r"^(cnr-[0-9a-f]{12}-e[0-9]{10})-pg$")

_PLAN_SEAL = object()
_RESPONSE_SEAL = object()
_RESULT_SEAL = object()


class ControllerReceiptV2ArmObservationFailure(RuntimeError):
    """Bounded refusal that contains no credential or provider content."""


def _fail(code: str) -> NoReturn:
    raise ControllerReceiptV2ArmObservationFailure(code) from None


def _read_policy() -> dict[str, Any]:
    descriptor = -1
    try:
        before = POLICY_PATH.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= MAXIMUM_POLICY_BYTES
        ):
            _fail("controller_receipt_arm.policy_invalid")
        descriptor = os.open(
            POLICY_PATH,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        after = os.fstat(descriptor)
        raw = os.read(descriptor, MAXIMUM_POLICY_BYTES + 1)
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(raw) != before.st_size
            or os.read(descriptor, 1)
            or hashlib.sha256(raw).hexdigest() != POLICY_SHA256
        ):
            _fail("controller_receipt_arm.policy_invalid")
    except ControllerReceiptV2ArmObservationFailure:
        raise
    except OSError:
        _fail("controller_receipt_arm.policy_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("controller_receipt_arm.policy_invalid")
    if type(value) is not dict:
        _fail("controller_receipt_arm.policy_invalid")
    return value


def validate_controller_receipt_v2_arm_observation_policy(
    policy: Any, *, executable: bool = False
) -> dict[str, Any]:
    template = _read_policy()
    try:
        document = json.loads(content._canonical(policy))
    except Exception:
        _fail("controller_receipt_arm.policy_invalid")
    transport = document.get("transport", {})
    claims = document.get("claims", {})
    upstream = document.get("upstream_policies", {})
    if (
        type(executable) is not bool
        or content._canonical(document) != content._canonical(template)
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-controller-receipt-v2-arm-observation-v1"
        or document.get("closed_world") is not True
        or document.get("activation_ready") is not False
        or any(document.get("activation", {}).values())
        or not document.get("activation_blockers")
        or upstream.get("controller_receipt_v2")
        != {
            "policy_id": "cosmaneura-staging-controller-receipt-v2",
            "sha256": f"sha256:{content.POLICY_SHA256}",
        }
        or upstream.get("production_authority")
        != {
            "policy_id": "cosmaneura-staging-controller-receipt-v2-production-v1",
            "sha256": f"sha256:{production.POLICY_SHA256}",
        }
        or upstream.get("restore_runtime_binding")
        != {
            "policy_id": "cosmaneura-staging-restore-runtime-binding-v1",
            "sha256": f"sha256:{runtime_binding.CANONICAL_POLICY_SHA256}",
        }
        or transport.get("method") != "GET"
        or transport.get("maximum_token_requests") != 1
        or transport.get("maximum_arm_get_requests")
        != len(document.get("observation", {}).get("exact_request_order", []))
        or any(
            transport.get(name) is not False
            for name in (
                "ambient_credentials_permitted",
                "ambient_proxy_permitted",
                "disk_materialization_permitted",
                "polling_permitted",
                "redirects_permitted",
                "retry_permitted",
            )
        )
        or claims
        != {
            "arm_network_facts_authenticated": True,
            "blob_or_redis_copy_absence_proven": False,
            "controller_receipt_exists": False,
            "create_only_behavior_proven": False,
            "database_facts_authenticated": False,
            "executable": False,
            "provider_facts_authenticated": False,
            "runtime_authority": False,
            "writer_identity_or_private_route_proven": False,
        }
    ):
        _fail("controller_receipt_arm.policy_invalid")
    if executable:
        _fail("controller_receipt_arm.policy_disabled")
    return document


def load_controller_receipt_v2_arm_observation_policy(
    *, executable: bool = False
) -> dict[str, Any]:
    return validate_controller_receipt_v2_arm_observation_policy(
        _read_policy(), executable=executable
    )


@dataclass(frozen=True, slots=True)
class _ArmRequest:
    key: str
    resource_id: str
    api_version: str


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _ArmPlan:
    requests: tuple[_ArmRequest, ...]
    provision_client_id: str
    sha256: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_arm.plan_invalid")

    def _is_valid(self) -> bool:
        return (
            type(self) is _ArmPlan
            and self._seal is _PLAN_SEAL
            and type(self.requests) is tuple
            and all(type(item) is _ArmRequest for item in self.requests)
            and self.sha256
            == content._digest(
                content._canonical(
                    [
                        {
                            "api_version": item.api_version,
                            "key": item.key,
                            "resource_id": item.resource_id,
                        }
                        for item in self.requests
                    ]
                )
            )
        )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _AuthenticatedArmResponse:
    key: str
    resource_id: str
    api_version: str
    body: bytes = dataclass_field(repr=False)
    body_sha256: str
    observed_at: datetime
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_arm.response_invalid")

    def _is_authenticated_for(self, request: _ArmRequest) -> bool:
        return (
            type(self) is _AuthenticatedArmResponse
            and self._seal is _RESPONSE_SEAL
            and type(self.body) is bytes
            and self.key == request.key
            and self.resource_id == request.resource_id
            and self.api_version == request.api_version
            and self.body_sha256 == content._digest(self.body)
            and type(self.observed_at) is datetime
            and self.observed_at.tzinfo is not None
            and self.observed_at.utcoffset() == timedelta(0)
            and self.observed_at.microsecond == 0
        )


def _resource(group: str, namespace: str, kind: str, name: str) -> str:
    return f"{group}/providers/{namespace}/{kind}/{name}"


def _child(parent: str, kind: str, name: str) -> str:
    return f"{parent}/{kind}/{name}"


def _configured(value: Any, predicate: Callable[[Any], bool]) -> bool:
    return (
        type(value) is str
        and PLACEHOLDER_RE.fullmatch(value) is None
        and predicate(value)
    )


def _scope(assignment_id: str) -> str:
    marker = "/providers/Microsoft.Authorization/roleAssignments/"
    before, separator, _name = assignment_id.rpartition(marker)
    return before if separator else ""


def _fresh_context(
    authority: production.ControllerReceiptV2ProductionAuthority,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        if (
            type(authority) is not production.ControllerReceiptV2ProductionAuthority
            or not authority._is_authenticated()
            or now < authority.validated_at
        ):
            _fail("controller_receipt_arm.production_authority_invalid")
        runtime, controller, production_policy = production._validate_context(
            runtime_binding._parse_policy(authority._runtime_policy),
            content._parse_canonical(
                authority._controller_policy, content.MAXIMUM_POLICY_BYTES
            ),
            content._parse_canonical(
                authority._production_policy, production.MAXIMUM_POLICY_BYTES
            ),
        )
        bindings = production._validate_prefix_records(
            runtime,
            controller,
            authority._launch_material,
            intent_bytes=authority.intent_bytes,
            submission_bytes=authority.submission_bytes,
            validated_at=now,
        )
        document = authority.authority
        if any(document.get(name) != bindings[name] for name in bindings):
            _fail("controller_receipt_arm.production_authority_invalid")
        return runtime, controller, production_policy, document
    except ControllerReceiptV2ArmObservationFailure:
        raise
    except Exception:
        _fail("controller_receipt_arm.production_authority_invalid")


def _derive_plan(
    authority: production.ControllerReceiptV2ProductionAuthority,
    *,
    now: datetime,
    policy: Mapping[str, Any],
) -> tuple[_ArmPlan, dict[str, Any]]:
    runtime, controller, _production_policy, document = _fresh_context(authority, now)
    request = document["request"]
    identity = document["identity"]
    source = controller["record_chain_source"]
    fixed = controller["fixed_bindings"]
    postgres = runtime["per_rehearsal_postgres"]
    runtime_env = runtime["runtime"]["container_apps_environment"]
    naming = policy["observation"]["naming"]
    match = TARGET_RE.fullmatch(str(request["target_server_name"]))
    if match is None:
        _fail("controller_receipt_arm.configuration_invalid")
    base = match.group(1)
    target_group = fixed["target_resource_group_id"]
    executor_group = fixed["executor_resource_group_id"]
    target_server = request["target_server_resource_id"]

    def network(group: str, kind: str, suffix: str) -> str:
        return _resource(group, "Microsoft.Network", kind, base + naming[suffix])

    def zone(group: str) -> str:
        return _resource(
            group, "Microsoft.Network", "privateDnsZones", naming["private_dns_zone"]
        )

    target_vnet = network(target_group, "virtualNetworks", "target_vnet_suffix")
    target_subnet = _child(
        target_vnet, "subnets", naming["target_private_endpoint_subnet"]
    )
    target_pe = network(
        target_group, "privateEndpoints", "target_private_endpoint_suffix"
    )
    target_nic = network(
        target_group, "networkInterfaces", "target_network_interface_suffix"
    )
    target_zone = zone(target_group)
    target_link = _child(
        target_zone, "virtualNetworkLinks", base + naming["target_dns_link_suffix"]
    )
    target_group_id = _child(target_pe, "privateDnsZoneGroups", "default")
    control_vnet = runtime_env["control_vnet_resource_id"]
    executor_subnet = postgres["subnet_resource_id"]
    infrastructure_subnet = runtime_env["infrastructure_subnet_resource_id"]
    executor_pe = network(
        executor_group, "privateEndpoints", "executor_private_endpoint_suffix"
    )
    executor_nic = network(
        executor_group, "networkInterfaces", "executor_network_interface_suffix"
    )
    executor_zone = zone(executor_group)
    executor_link = _child(
        executor_zone,
        "virtualNetworkLinks",
        base + naming["executor_dns_link_suffix"],
    )
    executor_group_id = _child(executor_pe, "privateDnsZoneGroups", "default")
    resources = {
        "target_postgres_server": target_server,
        "target_vnet": target_vnet,
        "target_subnet": target_subnet,
        "target_private_endpoint": target_pe,
        "target_network_interface": target_nic,
        "target_private_dns_zone": target_zone,
        "target_private_dns_link": target_link,
        "target_private_dns_zone_group": target_group_id,
        "executor_control_vnet": control_vnet,
        "executor_subnet": executor_subnet,
        "executor_private_endpoint": executor_pe,
        "executor_network_interface": executor_nic,
        "executor_private_dns_zone": executor_zone,
        "executor_private_dns_link": executor_link,
        "executor_private_dns_zone_group": executor_group_id,
        "marker_storage_account": source["account_resource_id"],
        "controller_receipt_container": source["container_resource_ids"][
            "controller_receipt"
        ],
        "controller_receipt_immutability_policy": source[
            "controller_immutability_policy_resource_id"
        ],
        "marker_create_role_definition": source["create_role_definition_id"],
        "marker_read_role_definition": source["read_role_definition_id"],
        "marker_writer_role_assignment": source["writer_role_assignment_id"],
        "marker_provision_reader_role_assignment": source["reader_role_assignment_ids"][
            "provision"
        ],
        "marker_cleanup_reader_role_assignment": source["reader_role_assignment_ids"][
            "cleanup"
        ],
    }
    api = policy["observation"]["api_versions"]
    versions = {
        "target_postgres_server": api["postgres"],
        **{
            name: api["network"]
            for name in policy["observation"]["exact_request_order"][1:15]
        },
        **{
            name: api["private_dns"]
            for name in resources
            if "private_dns_zone" in name or "private_dns_link" in name
        },
        **{
            name: api["storage"]
            for name in (
                "marker_storage_account",
                "controller_receipt_container",
                "controller_receipt_immutability_policy",
            )
        },
        **{
            name: api["authorization"]
            for name in resources
            if "role_definition" in name or "role_assignment" in name
        },
    }
    expected_order = policy["observation"]["exact_request_order"]
    required_ids = tuple(resources[name] for name in expected_order)
    container = resources["controller_receipt_container"]
    role_definition_prefix = (
        f"/subscriptions/{identity['subscription_id']}"
        "/providers/Microsoft.Authorization/roleDefinitions/"
    )
    if (
        len(resources) != len(expected_order)
        or set(resources) != set(expected_order)
        or len(set(item.casefold() for item in required_ids)) != len(required_ids)
        or any(not _configured(item, content._azure_id) for item in required_ids)
        or any(
            item.split("/")[2].casefold() != identity["subscription_id"].casefold()
            for item in required_ids
        )
        or content._resource_group(target_server).casefold() != target_group.casefold()
        or content._parent_id(executor_subnet).casefold() != control_vnet.casefold()
        or not infrastructure_subnet.casefold().startswith(
            f"{control_vnet}/subnets/".casefold()
        )
        or content._parent_id(infrastructure_subnet).casefold()
        != control_vnet.casefold()
        or infrastructure_subnet.casefold() == executor_subnet.casefold()
        or content._resource_group(executor_subnet).casefold()
        != executor_group.casefold()
        or source["writer_identity_resource_id"]
        != identity["provision_job_uami_resource_id"]
        or source["reader_identity_resource_ids"]["provision"]
        != identity["provision_job_uami_resource_id"]
        or source["reader_identity_client_ids"]["provision"]
        != identity["provision_job_uami_client_id"]
        or any(
            _scope(resources[name]).casefold() != container.casefold()
            for name in (
                "marker_writer_role_assignment",
                "marker_provision_reader_role_assignment",
                "marker_cleanup_reader_role_assignment",
            )
        )
        or any(
            not resources[name].casefold().startswith(role_definition_prefix.casefold())
            for name in ("marker_create_role_definition", "marker_read_role_definition")
        )
    ):
        _fail("controller_receipt_arm.configuration_invalid")
    requests = tuple(
        _ArmRequest(name, resources[name], versions[name]) for name in expected_order
    )
    value = object.__new__(_ArmPlan)
    object.__setattr__(value, "requests", requests)
    object.__setattr__(
        value, "provision_client_id", identity["provision_job_uami_client_id"]
    )
    object.__setattr__(
        value,
        "sha256",
        content._digest(
            content._canonical(
                [
                    {
                        "api_version": item.api_version,
                        "key": item.key,
                        "resource_id": item.resource_id,
                    }
                    for item in requests
                ]
            )
        ),
    )
    object.__setattr__(value, "_seal", _PLAN_SEAL)
    if not value._is_valid():
        _fail("controller_receipt_arm.plan_invalid")
    return value, dict(
        authority=document, controller=controller, runtime=runtime, resources=resources
    )


class _ExactArmPointGetAdapter:
    __slots__ = (
        "_arm_opener",
        "_checkpoint",
        "_deadline_factory",
        "_lock",
        "_monotonic",
        "_plan",
        "_policy",
        "_token_source",
        "_used",
    )
    _lock: threading.Lock
    _used: bool

    def __init__(self) -> None:
        _fail("controller_receipt_arm.adapter_invalid")

    @classmethod
    def _from_reviewed_plan(
        cls,
        *,
        plan: _ArmPlan,
        policy: Mapping[str, Any],
        token_source: adapters.ManagedIdentityTokenSource,
        arm_opener: adapters._HttpOpener,
        checkpoint: Callable[[], datetime],
        monotonic: Callable[[], float],
        deadline_factory: Callable[[int], Any],
    ) -> _ExactArmPointGetAdapter:
        if (
            cls is not _ExactArmPointGetAdapter
            or not plan._is_valid()
            or type(token_source) is not adapters.ManagedIdentityTokenSource
            or token_source._client_id != plan.provision_client_id
            or validate_controller_receipt_v2_arm_observation_policy(policy) != policy
            or not callable(getattr(arm_opener, "open", None))
            or not all(
                callable(item) for item in (checkpoint, monotonic, deadline_factory)
            )
            or len(plan.requests) != policy["transport"]["maximum_arm_get_requests"]
        ):
            _fail("controller_receipt_arm.adapter_invalid")
        value = object.__new__(cls)
        for name, item in {
            "_plan": plan,
            "_policy": policy,
            "_token_source": token_source,
            "_arm_opener": arm_opener,
            "_checkpoint": checkpoint,
            "_monotonic": monotonic,
            "_deadline_factory": deadline_factory,
            "_lock": threading.Lock(),
            "_used": False,
        }.items():
            object.__setattr__(value, name, item)
        return value

    def read_all(self) -> tuple[_AuthenticatedArmResponse, ...]:
        with self._lock:
            if self._used:
                _fail("controller_receipt_arm.already_used")
            self._used = True
        transport = self._policy["transport"]
        try:
            started = float(self._monotonic())
        except Exception:
            _fail("controller_receipt_arm.clock_invalid")
        deadline = started + transport["maximum_elapsed_seconds"]

        def checkpoint() -> datetime:
            try:
                if float(self._monotonic()) > deadline:
                    _fail("controller_receipt_arm.deadline")
            except ControllerReceiptV2ArmObservationFailure:
                raise
            except Exception:
                _fail("controller_receipt_arm.clock_invalid")
            return self._checkpoint()

        checkpoint()
        try:
            token = self._token_source.get_token(
                resource=transport["oauth_resource"],
                timeout_seconds=transport["request_timeout_seconds"],
            )
        except Exception:
            _fail("controller_receipt_arm.identity_failed")
        checkpoint()
        responses: list[_AuthenticatedArmResponse] = []
        for request_item in self._plan.requests:
            checkpoint()
            query = urllib.parse.urlencode({"api-version": request_item.api_version})
            url = f"{transport['management_origin']}{request_item.resource_id}?{query}"
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": transport["response_media_type"],
                    "Accept-Encoding": "identity",
                    "Authorization": f"Bearer {token}",
                },
                method="GET",
            )
            try:
                with self._deadline_factory(transport["request_timeout_seconds"]):
                    with self._arm_opener.open(
                        request, timeout=transport["request_timeout_seconds"]
                    ) as response:
                        status, _media, raw = adapters._read_json_response(
                            response,
                            expected_url=url,
                            maximum_bytes=transport["maximum_response_bytes"],
                        )
            except urllib.error.HTTPError as exc:
                if 300 <= int(exc.code) < 400:
                    _fail("controller_receipt_arm.redirect_refused")
                _fail("controller_receipt_arm.provider_refused")
            except ControllerReceiptV2ArmObservationFailure:
                raise
            except Exception:
                _fail("controller_receipt_arm.provider_unavailable")
            if status != 200:
                _fail("controller_receipt_arm.provider_refused")
            try:
                adapters._load_mapping(raw, "controller_receipt_arm.response_invalid")
            except Exception:
                _fail("controller_receipt_arm.response_invalid")
            observed_at = checkpoint()
            item = object.__new__(_AuthenticatedArmResponse)
            for name, value in {
                "key": request_item.key,
                "resource_id": request_item.resource_id,
                "api_version": request_item.api_version,
                "body": bytes(raw),
                "body_sha256": content._digest(raw),
                "observed_at": observed_at,
                "_seal": _RESPONSE_SEAL,
            }.items():
                object.__setattr__(item, name, value)
            responses.append(item)
        return tuple(responses)


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("controller_receipt_arm.response_invalid")
    return value


def _array(value: Any) -> list[Any]:
    if type(value) is not list:
        _fail("controller_receipt_arm.response_invalid")
    return value


def _lower_id(value: Any) -> str:
    if not content._azure_id(value):
        _fail("controller_receipt_arm.response_invalid")
    return str(value).casefold()


def _same_id(left: Any, right: str) -> bool:
    try:
        return _lower_id(left) == _lower_id(right)
    except ControllerReceiptV2ArmObservationFailure:
        return False


def _properties(
    document: Mapping[str, Any], request: _ArmRequest, kind: str
) -> Mapping[str, Any]:
    if (
        not _same_id(document.get("id"), request.resource_id)
        or str(document.get("name", "")).casefold()
        != request.resource_id.rsplit("/", 1)[-1].casefold()
        or str(document.get("type", "")).casefold() != kind.casefold()
    ):
        _fail("controller_receipt_arm.response_invalid")
    return _mapping(document.get("properties"))


def _permission_digest(properties: Mapping[str, Any]) -> str:
    permissions = _array(properties.get("permissions"))
    keys = ("actions", "notActions", "dataActions", "notDataActions")
    normalized: list[dict[str, Any]] = []
    for item in permissions:
        entry = _mapping(item)
        values: dict[str, Any] = {}
        for key in keys:
            items = _array(entry.get(key, []))
            if any(type(value) is not str for value in items):
                _fail("controller_receipt_arm.response_invalid")
            values[key] = sorted(items)
        normalized.append(values)
    return content._digest(
        content._canonical(sorted(normalized, key=content._canonical))
    )


def _validate_endpoint(
    document: Mapping[str, Any],
    request: _ArmRequest,
    *,
    server_id: str,
    subnet_id: str,
    nic_id: str,
    group_id: str,
) -> None:
    properties = _properties(document, request, "Microsoft.Network/privateEndpoints")
    connections = _array(properties.get("privateLinkServiceConnections"))
    interfaces = _array(properties.get("networkInterfaces"))
    if (
        not _same_id(_mapping(properties.get("subnet")).get("id"), subnet_id)
        or len(connections) != 1
        or len(interfaces) != 1
        or not _same_id(_mapping(interfaces[0]).get("id"), nic_id)
    ):
        _fail("controller_receipt_arm.topology_invalid")
    connection = _mapping(connections[0])
    state = _mapping(connection.get("privateLinkServiceConnectionState"))
    if (
        not _same_id(connection.get("privateLinkServiceId"), server_id)
        or connection.get("groupIds") != [group_id]
        or state.get("status") != "Approved"
    ):
        _fail("controller_receipt_arm.topology_invalid")


def _validate_nic(
    doc: Mapping[str, Any], request: _ArmRequest, *, subnet: str, cidr: str
) -> str:
    properties = _properties(doc, request, "Microsoft.Network/networkInterfaces")
    configurations = _array(properties.get("ipConfigurations"))
    if len(configurations) != 1:
        _fail("controller_receipt_arm.topology_invalid")
    config = _mapping(_mapping(configurations[0]).get("properties"))
    private_ip = config.get("privateIPAddress")
    try:
        address = ipaddress.IPv4Address(private_ip)
    except (TypeError, ValueError):
        _fail("controller_receipt_arm.topology_invalid")
    if (
        type(private_ip) is not str
        or address not in ipaddress.IPv4Network(cidr, strict=True)
        or not _same_id(_mapping(config.get("subnet")).get("id"), subnet)
        or config.get("publicIPAddress") is not None
    ):
        _fail("controller_receipt_arm.topology_invalid")
    return private_ip


def _validate_subnet(
    document: Mapping[str, Any],
    request: _ArmRequest,
    *,
    prefix: str,
) -> None:
    properties = _properties(
        document, request, "Microsoft.Network/virtualNetworks/subnets"
    )
    if (
        properties.get("addressPrefix") != prefix
        or properties.get("privateEndpointNetworkPolicies") != "Disabled"
        or properties.get("defaultOutboundAccess") is not False
        or any(
            properties.get(name) is not None
            for name in ("routeTable", "natGateway", "networkSecurityGroup")
        )
        or any(
            properties.get(name) not in (None, [])
            for name in ("serviceEndpoints", "serviceEndpointPolicies", "delegations")
        )
    ):
        _fail("controller_receipt_arm.topology_invalid")


def _validate_vnet(
    document: Mapping[str, Any],
    request: _ArmRequest,
    *,
    subnet_id: str,
    prefix: str | None,
) -> None:
    properties = _properties(document, request, "Microsoft.Network/virtualNetworks")
    subnets = _array(properties.get("subnets"))
    if not any(_same_id(_mapping(item).get("id"), subnet_id) for item in subnets):
        _fail("controller_receipt_arm.topology_invalid")
    if prefix is not None and _mapping(properties.get("addressSpace")).get(
        "addressPrefixes"
    ) != [prefix]:
        _fail("controller_receipt_arm.topology_invalid")
    if _mapping(properties.get("dhcpOptions", {})).get("dnsServers") not in (None, []):
        _fail("controller_receipt_arm.topology_invalid")


def _validate_dns(
    documents: Mapping[str, Mapping[str, Any]],
    requests: Mapping[str, _ArmRequest],
    *,
    prefix: str,
    vnet_id: str,
    zone_id: str,
) -> None:
    zone_request = requests[f"{prefix}_private_dns_zone"]
    zone = documents[zone_request.key]
    _properties(zone, zone_request, "Microsoft.Network/privateDnsZones")
    if str(zone.get("location", "")).casefold() != "global":
        _fail("controller_receipt_arm.topology_invalid")
    link_request = requests[f"{prefix}_private_dns_link"]
    link = _properties(
        documents[link_request.key],
        link_request,
        "Microsoft.Network/privateDnsZones/virtualNetworkLinks",
    )
    group_request = requests[f"{prefix}_private_dns_zone_group"]
    group = _properties(
        documents[group_request.key],
        group_request,
        "Microsoft.Network/privateEndpoints/privateDnsZoneGroups",
    )
    configs = _array(group.get("privateDnsZoneConfigs"))
    if (
        not _same_id(_mapping(link.get("virtualNetwork")).get("id"), vnet_id)
        or link.get("registrationEnabled") is not False
        or link.get("provisioningState") != "Succeeded"
        or len(configs) != 1
        or not _same_id(
            _mapping(_mapping(configs[0]).get("properties")).get("privateDnsZoneId"),
            zone_id,
        )
    ):
        _fail("controller_receipt_arm.topology_invalid")


def _validate_observation(
    authority: production.ControllerReceiptV2ProductionAuthority,
    policy: Mapping[str, Any],
    plan: _ArmPlan,
    responses: tuple[_AuthenticatedArmResponse, ...],
    *,
    started_at: datetime,
    observed_at: datetime,
) -> dict[str, Any]:
    expected_plan, context = _derive_plan(authority, now=observed_at, policy=policy)
    if (
        plan != expected_plan
        or not plan._is_valid()
        or len(responses) != len(plan.requests)
    ):
        _fail("controller_receipt_arm.response_invalid")
    documents: dict[str, Mapping[str, Any]] = {}
    for request, response in zip(plan.requests, responses, strict=True):
        if (
            not response._is_authenticated_for(request)
            or not started_at <= response.observed_at <= observed_at
        ):
            _fail("controller_receipt_arm.response_invalid")
        try:
            documents[request.key] = adapters._load_mapping(
                response.body, "controller_receipt_arm.response_invalid"
            )
        except Exception:
            _fail("controller_receipt_arm.response_invalid")
    requests = {item.key: item for item in plan.requests}
    resources = context["resources"]
    authority_document = context["authority"]
    controller = context["controller"]
    runtime = context["runtime"]
    runtime_env = runtime["runtime"]["container_apps_environment"]
    intent = content._parse_canonical(
        authority.intent_bytes, content.MAXIMUM_POLICY_BYTES
    )
    expected_tags = intent["required_tags"]
    server_request = requests["target_postgres_server"]
    server_document = documents["target_postgres_server"]
    server = _properties(
        server_document, server_request, "Microsoft.DBforPostgreSQL/flexibleServers"
    )
    network = _mapping(server.get("network"))
    postgres_policy = policy["observation"]["postgres"]
    target_fqdn = f"{authority_document['request']['target_server_name']}{controller['fixed_bindings']['target_postgres_fqdn_suffix']}"
    if (
        server_document.get("location") != authority_document["request"]["location"]
        or server_document.get("tags") != expected_tags
        or server.get("state") != postgres_policy["provisioning_state"]
        or server.get("fullyQualifiedDomainName") != target_fqdn
        or server.get("createMode") != postgres_policy["create_mode"]
        or not _same_id(
            server.get("sourceServerResourceId"),
            authority_document["source"]["source_server_resource_id"],
        )
        or server.get("pointInTimeUTC")
        != authority_document["source"]["approved_restore_time"]
        or network.get("publicNetworkAccess")
        != postgres_policy["public_network_access"]
    ):
        _fail("controller_receipt_arm.postgres_invalid")
    target_network = policy["observation"]["target_network"]
    target_cidr = target_network["private_endpoint_subnet_prefix"]
    executor_cidr = runtime["per_rehearsal_postgres"]["subnet_cidr"]
    _validate_vnet(
        documents["target_vnet"],
        requests["target_vnet"],
        subnet_id=resources["target_subnet"],
        prefix=target_network["vnet_address_prefix"],
    )
    _validate_subnet(
        documents["target_subnet"],
        requests["target_subnet"],
        prefix=target_cidr,
    )
    _validate_vnet(
        documents["executor_control_vnet"],
        requests["executor_control_vnet"],
        subnet_id=resources["executor_subnet"],
        prefix=None,
    )
    _validate_subnet(
        documents["executor_subnet"],
        requests["executor_subnet"],
        prefix=executor_cidr,
    )
    private_ips: dict[str, str] = {}
    for side, cidr in (("target", target_cidr), ("executor", executor_cidr)):
        _validate_endpoint(
            documents[f"{side}_private_endpoint"],
            requests[f"{side}_private_endpoint"],
            server_id=resources["target_postgres_server"],
            subnet_id=resources[f"{side}_subnet"],
            nic_id=resources[f"{side}_network_interface"],
            group_id=postgres_policy["private_link_group_id"],
        )
        private_ips[side] = _validate_nic(
            documents[f"{side}_network_interface"],
            requests[f"{side}_network_interface"],
            subnet=resources[f"{side}_subnet"],
            cidr=cidr,
        )
        _validate_dns(
            documents,
            requests,
            prefix=side,
            vnet_id=(
                resources["target_vnet"]
                if side == "target"
                else resources["executor_control_vnet"]
            ),
            zone_id=resources[f"{side}_private_dns_zone"],
        )
    marker_policy = policy["observation"]["marker"]
    account_request = requests["marker_storage_account"]
    account = _properties(
        documents["marker_storage_account"],
        account_request,
        "Microsoft.Storage/storageAccounts",
    )
    container_request = requests["controller_receipt_container"]
    container = _properties(
        documents["controller_receipt_container"],
        container_request,
        "Microsoft.Storage/storageAccounts/blobServices/containers",
    )
    immutable_request = requests["controller_receipt_immutability_policy"]
    immutable = _properties(
        documents["controller_receipt_immutability_policy"],
        immutable_request,
        "Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies",
    )
    if (
        account.get("publicNetworkAccess") != marker_policy["public_network_access"]
        or account.get("allowSharedKeyAccess") is not marker_policy["shared_key_access"]
        or account.get("allowBlobPublicAccess") is not False
        or account.get("supportsHttpsTrafficOnly") is not True
        or account.get("minimumTlsVersion") != "TLS1_2"
        or account.get("defaultToOAuthAuthentication") is not True
        or container.get("publicAccess") != "None"
        or container.get("hasImmutabilityPolicy") is not True
        or container.get("hasLegalHold") is not marker_policy["legal_hold_required"]
        or immutable.get("state") != marker_policy["immutability_mode"]
        or type(immutable.get("immutabilityPeriodSinceCreationInDays")) is not int
        or immutable["immutabilityPeriodSinceCreationInDays"]
        < marker_policy["minimum_retention_days"]
        or immutable.get("allowProtectedAppendWrites") is not False
        or immutable.get("allowProtectedAppendWritesAll") is not False
    ):
        _fail("controller_receipt_arm.marker_invalid")
    source = controller["record_chain_source"]
    role_expectations = {
        "marker_create_role_definition": source["create_role_permissions_sha256"],
        "marker_read_role_definition": source["read_role_permissions_sha256"],
    }
    for key, digest in role_expectations.items():
        properties = _properties(
            documents[key], requests[key], "Microsoft.Authorization/roleDefinitions"
        )
        if _permission_digest(properties) != digest:
            _fail("controller_receipt_arm.rbac_invalid")
    assignments = {
        "marker_writer_role_assignment": (
            authority_document["identity"]["provision_job_uami_principal_id"],
            resources["marker_create_role_definition"],
        ),
        "marker_provision_reader_role_assignment": (
            authority_document["identity"]["provision_job_uami_principal_id"],
            resources["marker_read_role_definition"],
        ),
        "marker_cleanup_reader_role_assignment": (
            runtime["identities"]["jobs"]["cleanup"]["principal_id"],
            resources["marker_read_role_definition"],
        ),
    }
    for key, (principal, role_definition) in assignments.items():
        properties = _properties(
            documents[key], requests[key], "Microsoft.Authorization/roleAssignments"
        )
        if (
            properties.get("principalId") != principal
            or properties.get("principalType") != "ServicePrincipal"
            or not _same_id(properties.get("roleDefinitionId"), role_definition)
            or not _same_id(
                properties.get("scope"), resources["controller_receipt_container"]
            )
            or properties.get("condition") is not None
            or properties.get("delegatedManagedIdentityResourceId") is not None
        ):
            _fail("controller_receipt_arm.rbac_invalid")
    target = {
        "target_fqdn": target_fqdn,
        "target_postgres_server_id": resources["target_postgres_server"],
        "target_vnet_id": resources["target_vnet"],
        "target_subnet_id": resources["target_subnet"],
        "target_private_endpoint_id": resources["target_private_endpoint"],
        "target_private_ip_address": private_ips["target"],
        "target_network_interface_id": resources["target_network_interface"],
        "target_private_dns_zone_id": resources["target_private_dns_zone"],
        "target_private_dns_link_id": resources["target_private_dns_link"],
        "target_private_dns_zone_group_id": resources["target_private_dns_zone_group"],
        "executor_control_vnet_id": resources["executor_control_vnet"],
        "executor_private_endpoint_subnet_id": resources["executor_subnet"],
        "executor_private_endpoint_subnet_cidr": executor_cidr,
        "configured_executor_infrastructure_subnet": {
            "id": runtime_env["infrastructure_subnet_resource_id"],
            "cidr": runtime_env["infrastructure_subnet_cidr"],
        },
        "executor_private_endpoint_id": resources["executor_private_endpoint"],
        "executor_private_ip_address": private_ips["executor"],
        "executor_network_interface_id": resources["executor_network_interface"],
        "executor_private_dns_zone_id": resources["executor_private_dns_zone"],
        "executor_private_dns_link_id": resources["executor_private_dns_link"],
        "executor_private_dns_zone_group_id": resources[
            "executor_private_dns_zone_group"
        ],
    }
    manifest = [
        {
            "api_version": response.api_version,
            "key": response.key,
            "observed_at": response.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "resource_id": response.resource_id,
            "response_sha256": response.body_sha256,
        }
        for response in responses
    ]
    return {
        "schema_version": 1,
        "kind": policy["output"]["kind"],
        "arm_observation_policy_id": policy["policy_id"],
        "arm_observation_policy_sha256": f"sha256:{POLICY_SHA256}",
        "production_authority_sha256": content._digest(authority.authority_bytes),
        "controller_policy_binding_sha256": content._policy_binding_digest(controller),
        "runtime_policy_binding_sha256": runtime_binding._policy_binding_digest(
            runtime
        ),
        "rehearsal_id": authority.rehearsal_id,
        "execution_resource_id": authority.execution_binding,
        "intent_sha256": authority.intent_sha256,
        "submission_sha256": authority.submission_sha256,
        "source_restore_binding": {
            "source_server_resource_id": authority_document["source"][
                "source_server_resource_id"
            ],
            "create_mode": postgres_policy["create_mode"],
            "point_in_time_utc": authority_document["source"]["approved_restore_time"],
        },
        "target": target,
        "marker": {
            "storage_account_resource_id": resources["marker_storage_account"],
            "controller_receipt_container_resource_id": resources[
                "controller_receipt_container"
            ],
            "immutability_policy_resource_id": resources[
                "controller_receipt_immutability_policy"
            ],
            "public_network_access": account["publicNetworkAccess"],
            "shared_key_access": account["allowSharedKeyAccess"],
            "legal_hold": container["hasLegalHold"],
            "immutability_mode": immutable["state"],
            "retention_days": immutable["immutabilityPeriodSinceCreationInDays"],
        },
        "rbac": {
            "create_role_definition_id": resources["marker_create_role_definition"],
            "create_role_permissions_sha256": source["create_role_permissions_sha256"],
            "read_role_definition_id": resources["marker_read_role_definition"],
            "read_role_permissions_sha256": source["read_role_permissions_sha256"],
            "writer_role_assignment_id": resources["marker_writer_role_assignment"],
            "reader_role_assignment_ids": {
                "provision": resources["marker_provision_reader_role_assignment"],
                "cleanup": resources["marker_cleanup_reader_role_assignment"],
            },
        },
        "request_plan_sha256": plan.sha256,
        "observation_manifest_sha256": content._digest(content._canonical(manifest)),
        "observation_started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": min(
            authority_document["launch"]["launch_expires_at"],
            authority_document["request"]["cleanup_not_after"],
        ),
        "claims": dict(policy["claims"]),
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedControllerReceiptV2ArmNetworkObservation:
    """Sealed selected ARM facts; never a complete provider/runtime authority."""

    authority_bytes: bytes = dataclass_field(repr=False)
    rehearsal_id: str
    execution_resource_id: str
    observed_at: datetime
    arm_network_facts_authenticated: bool
    provider_facts_authenticated: bool
    controller_receipt_exists: bool
    runtime_authority: bool
    executable: bool
    _production_authority: production.ControllerReceiptV2ProductionAuthority = (
        dataclass_field(repr=False, compare=False)
    )
    _policy_bytes: bytes = dataclass_field(repr=False, compare=False)
    _responses: tuple[_AuthenticatedArmResponse, ...] = dataclass_field(
        repr=False, compare=False
    )
    _started_at: datetime = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_arm.authority_unsealed")

    @property
    def authority(self) -> dict[str, Any]:
        if not self._is_authenticated():
            _fail("controller_receipt_arm.authority_invalid")
        return content._parse_canonical(self.authority_bytes, MAXIMUM_POLICY_BYTES)

    def _is_authenticated(self) -> bool:
        try:
            policy = validate_controller_receipt_v2_arm_observation_policy(
                content._parse_canonical(self._policy_bytes, MAXIMUM_POLICY_BYTES)
            )
            plan, _context = _derive_plan(
                self._production_authority, now=self.observed_at, policy=policy
            )
            expected = _validate_observation(
                self._production_authority,
                policy,
                plan,
                self._responses,
                started_at=self._started_at,
                observed_at=self.observed_at,
            )
            return (
                type(self) is AuthenticatedControllerReceiptV2ArmNetworkObservation
                and self._seal is _RESULT_SEAL
                and self.authority_bytes == content._canonical(expected)
                and self.rehearsal_id == expected["rehearsal_id"]
                and self.execution_resource_id == expected["execution_resource_id"]
                and self.arm_network_facts_authenticated is True
                and self.provider_facts_authenticated is False
                and self.controller_receipt_exists is False
                and self.runtime_authority is False
                and self.executable is False
            )
        except Exception:
            return False


def _mint_observation(
    *,
    production_authority: production.ControllerReceiptV2ProductionAuthority,
    policy: Mapping[str, Any],
    plan: _ArmPlan,
    responses: tuple[_AuthenticatedArmResponse, ...],
    started_at: datetime,
    observed_at: datetime,
) -> AuthenticatedControllerReceiptV2ArmNetworkObservation:
    document = _validate_observation(
        production_authority,
        policy,
        plan,
        responses,
        started_at=started_at,
        observed_at=observed_at,
    )
    value = object.__new__(AuthenticatedControllerReceiptV2ArmNetworkObservation)
    fields = {
        "authority_bytes": content._canonical(document),
        "rehearsal_id": document["rehearsal_id"],
        "execution_resource_id": document["execution_resource_id"],
        "observed_at": observed_at,
        "arm_network_facts_authenticated": True,
        "provider_facts_authenticated": False,
        "controller_receipt_exists": False,
        "runtime_authority": False,
        "executable": False,
        "_production_authority": production_authority,
        "_policy_bytes": content._canonical(policy),
        "_responses": responses,
        "_started_at": started_at,
        "_seal": _RESULT_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    if not value._is_authenticated():
        _fail("controller_receipt_arm.authority_invalid")
    return value


class ManagedIdentityControllerReceiptV2ArmObserver:
    """One-shot exact GET observer; the public call has one sealed input."""

    __slots__ = (
        "_arm_opener",
        "_clock",
        "_deadline_factory",
        "_epoch_clock",
        "_identity_opener",
        "_lock",
        "_monotonic",
        "_used",
    )
    _lock: threading.Lock
    _used: bool

    def __init__(self) -> None:
        _fail("controller_receipt_arm.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        identity_opener: adapters._HttpOpener,
        arm_opener: adapters._HttpOpener,
        epoch_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        deadline_factory: Callable[[int], Any] = adapters._wall_clock_deadline,
    ) -> ManagedIdentityControllerReceiptV2ArmObserver:
        if (
            cls is not ManagedIdentityControllerReceiptV2ArmObserver
            or not all(
                callable(item)
                for item in (clock, epoch_clock, monotonic, deadline_factory)
            )
            or not all(
                callable(getattr(item, "open", None))
                for item in (identity_opener, arm_opener)
            )
        ):
            _fail("controller_receipt_arm.configuration_invalid")
        value = object.__new__(cls)
        for name, item in {
            "_clock": clock,
            "_identity_opener": identity_opener,
            "_arm_opener": arm_opener,
            "_epoch_clock": epoch_clock,
            "_monotonic": monotonic,
            "_deadline_factory": deadline_factory,
            "_lock": threading.Lock(),
            "_used": False,
        }.items():
            object.__setattr__(value, name, item)
        return value

    def observe(
        self,
        *,
        production_authority: production.ControllerReceiptV2ProductionAuthority,
    ) -> AuthenticatedControllerReceiptV2ArmNetworkObservation:
        with self._lock:
            if self._used:
                _fail("controller_receipt_arm.already_used")
            self._used = True
        policy = load_controller_receipt_v2_arm_observation_policy()
        trusted_utc = ingress._TrustedUtcTimeline(self._clock)
        started_at = trusted_utc.read()
        plan, _context = _derive_plan(
            production_authority, now=started_at, policy=policy
        )

        def checkpoint() -> datetime:
            now = trusted_utc.read()
            current, _current_context = _derive_plan(
                production_authority, now=now, policy=policy
            )
            if current != plan:
                _fail("controller_receipt_arm.production_authority_invalid")
            return now

        try:
            token_source = adapters.ManagedIdentityTokenSource.from_environment(
                client_id=plan.provision_client_id,
                opener=self._identity_opener,
                clock=self._epoch_clock,
            )
            reader = _ExactArmPointGetAdapter._from_reviewed_plan(
                plan=plan,
                policy=policy,
                token_source=token_source,
                arm_opener=self._arm_opener,
                checkpoint=checkpoint,
                monotonic=self._monotonic,
                deadline_factory=self._deadline_factory,
            )
            responses = reader.read_all()
            observed_at = checkpoint()
            return _mint_observation(
                production_authority=production_authority,
                policy=policy,
                plan=plan,
                responses=responses,
                started_at=started_at,
                observed_at=observed_at,
            )
        except ControllerReceiptV2ArmObservationFailure:
            raise
        except Exception:
            _fail("controller_receipt_arm.observation_failed")


def build_controller_receipt_v2_arm_observer() -> NoReturn:
    load_controller_receipt_v2_arm_observation_policy(executable=True)
    _fail("controller_receipt_arm.policy_disabled")


if __name__ == "__main__":
    load_controller_receipt_v2_arm_observation_policy(
        executable="--executable" in sys.argv
    )
    print("controller receipt v2 ARM observation policy valid (dormant)")
