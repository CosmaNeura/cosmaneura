#!/usr/bin/env python3
"""Dormant point-in-time isolation inventory for controller-receipt-v2."""

from __future__ import annotations

import hashlib
import json
import math
import os
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
    from scripts import observe_staging_controller_receipt_v2_arm as arm
    from scripts import observe_staging_controller_receipt_v2_database as database
    from scripts import read_staging_controller_receipt_v2 as ingress
    from scripts import staging_restore_runtime_adapters as adapters
    from scripts import validate_staging_controller_receipt_v2 as content
    from scripts import validate_staging_controller_receipt_v2_production as production
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import observe_staging_controller_receipt_v2_arm as arm
    import observe_staging_controller_receipt_v2_database as database
    import read_staging_controller_receipt_v2 as ingress
    import staging_restore_runtime_adapters as adapters
    import validate_staging_controller_receipt_v2 as content
    import validate_staging_controller_receipt_v2_production as production
    import validate_staging_restore_runtime_binding as runtime_binding


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/controller-receipt-v2-isolation-inventory-policy.json"
)
POLICY_SHA256 = "c0cbcafe2e9746bfd6db2f3fa5fc4443c8e75824e0ca2a6dbe79b3f30572b749"
MAXIMUM_POLICY_BYTES = 64 * 1024

_PLAN_SEAL = object()
_RESPONSE_SEAL = object()
_AUTHORITY_SEAL = object()
_FAILURE_CODES = frozenset(
    f"controller_receipt_isolation.{suffix}"
    for suffix in "already_used authority_invalid authority_unsealed clock_invalid configuration_invalid deadline identity_failed inventory_invalid isolation_present observation_failed plan_invalid policy_disabled policy_invalid provider_refused provider_unavailable redirect_refused response_invalid".split()
)


class ControllerReceiptV2IsolationInventoryFailure(RuntimeError):
    """Bounded refusal that contains no credential or provider response content."""


def _fail(code: str) -> NoReturn:
    raise ControllerReceiptV2IsolationInventoryFailure(code) from None


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
            _fail("controller_receipt_isolation.policy_invalid")
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
            _fail("controller_receipt_isolation.policy_invalid")
    except ControllerReceiptV2IsolationInventoryFailure:
        raise
    except OSError:
        _fail("controller_receipt_isolation.policy_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("controller_receipt_isolation.policy_invalid")
    if type(value) is not dict:
        _fail("controller_receipt_isolation.policy_invalid")
    return value


def validate_controller_receipt_v2_isolation_inventory_policy(
    policy: Any, *, executable: bool = False
) -> dict[str, Any]:
    template = _read_policy()
    try:
        document = json.loads(content._canonical(policy))
    except Exception:
        _fail("controller_receipt_isolation.policy_invalid")
    observation = document.get("observation", {})
    inventory = observation.get("inventory", {})
    transport = document.get("transport", {})
    upstream = document.get("upstream_policies", {})
    pins = {
        "controller_receipt_v2": (
            "cosmaneura-staging-controller-receipt-v2",
            content.POLICY_SHA256,
        ),
        "restore_runtime_binding": (
            "cosmaneura-staging-restore-runtime-binding-v1",
            runtime_binding.CANONICAL_POLICY_SHA256,
        ),
        "production_authority": (
            "cosmaneura-staging-controller-receipt-v2-production-v1",
            production.POLICY_SHA256,
        ),
        "arm_observation": (
            "cosmaneura-staging-controller-receipt-v2-arm-observation-v1",
            arm.POLICY_SHA256,
        ),
        "database_observation": (
            "cosmaneura-staging-controller-receipt-v2-database-observation-v1",
            database.POLICY_SHA256,
        ),
    }
    if (
        type(executable) is not bool
        or content._canonical(document) != content._canonical(template)
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-controller-receipt-v2-isolation-inventory-observation-v1"
        or document.get("closed_world") is not True
        or document.get("activation_ready") is not False
        or not document.get("activation_blockers")
        or document.get("activation")
        != {
            "consumer_binding_implemented": False,
            "controller_receipt_builder_integrated": False,
            "executable": False,
            "isolation_inventory_observer_implemented": True,
            "live_arm_calls_enabled": False,
            "workflow_integrated": False,
        }
        or document.get("input")
        != {
            "caller_clock_path_policy_resource_id_uri_token_transport_or_inventory_selector_permitted": False,
            "kind": "authenticated-controller-receipt-v2-database-observation-v1",
            "only_public_input": "database_observation",
        }
        or upstream
        != {
            key: {"policy_id": policy_id, "sha256": f"sha256:{digest}"}
            for key, (policy_id, digest) in pins.items()
        }
        or observation.get("exact_request_order")
        != [
            "firewall_rules",
            "vnet_peerings",
            "public_ip_addresses",
            "resource_group_inventory",
        ]
        or inventory.get("required_top_level_resource_keys")
        != [
            "target_postgres_server",
            "target_vnet",
            "target_private_endpoint",
            "target_network_interface",
            "target_private_dns_zone",
        ]
        or inventory.get("optional_nested_resource_keys")
        != ["target_private_dns_link", "target_private_dns_zone_group"]
        or inventory.get("storage_resource_types")
        != ["Microsoft.Storage/storageAccounts"]
        or inventory.get("redis_resource_types")
        != ["Microsoft.Cache/Redis", "Microsoft.Cache/redisEnterprise"]
        or inventory.get("subnet_resource_group_listing_expected") is not False
        or transport.get("method") != "GET"
        or transport.get("maximum_token_requests") != 1
        or transport.get("maximum_arm_get_requests") != 4
        or transport.get("oauth_resource") != adapters.ARM_RESOURCE
        or transport.get("request_timeout_seconds") != 30
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
        or document.get("claims")
        != {
            "blob_or_redis_copy_absence_proven": False,
            "controller_receipt_exists": False,
            "create_only_behavior_proven": False,
            "database_mutation_authority": False,
            "executable": False,
            "execution_history_authenticated": False,
            "point_in_time_isolation_inventory_authenticated": True,
            "provider_facts_authenticated": False,
            "public_access_authenticated": True,
            "runtime_authority": False,
            "sanitizer_authority": False,
            "writer_identity_use_authenticated": False,
        }
    ):
        _fail("controller_receipt_isolation.policy_invalid")
    if executable:
        _fail("controller_receipt_isolation.policy_disabled")
    return document


def load_controller_receipt_v2_isolation_inventory_policy(
    *, executable: bool = False
) -> dict[str, Any]:
    return validate_controller_receipt_v2_isolation_inventory_policy(
        _read_policy(), executable=executable
    )


class _Timeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], float]) -> None:
        if not callable(source):
            _fail("controller_receipt_isolation.configuration_invalid")
        self._source = source
        self._last: float | None = None

    def read(self) -> float:
        try:
            value = self._source()
        except Exception:
            _fail("controller_receipt_isolation.clock_invalid")
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            _fail("controller_receipt_isolation.clock_invalid")
        current = float(value)
        if self._last is not None and current < self._last:
            _fail("controller_receipt_isolation.clock_invalid")
        self._last = current
        return current


def _remaining(timeline: _Timeline, deadline: float, maximum: int) -> int:
    value = math.floor(deadline - timeline.read())
    if value < 1:
        _fail("controller_receipt_isolation.deadline")
    return min(value, maximum)


@dataclass(frozen=True, slots=True)
class _Request:
    key: str
    resource_id: str
    api_version: str


@dataclass(frozen=True, slots=True)
class _KnownResource:
    key: str
    resource_id: str
    resource_type: str
    location: str
    required: bool


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _Plan:
    requests: tuple[_Request, ...]
    known_resources: tuple[_KnownResource, ...]
    provision_client_id: str
    target_resource_group_id: str
    target_location: str
    rehearsal_id: str
    execution_resource_id: str
    expires_at: datetime
    database_observation_sha256: str
    arm_observation_sha256: str
    production_authority_sha256: str
    controller_policy_binding_sha256: str
    runtime_policy_binding_sha256: str
    sha256: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_isolation.plan_invalid")

    def _document(self) -> dict[str, Any]:
        return {
            "arm_observation_sha256": self.arm_observation_sha256,
            "controller_policy_binding_sha256": self.controller_policy_binding_sha256,
            "database_observation_sha256": self.database_observation_sha256,
            "execution_resource_id": self.execution_resource_id,
            "expires_at": self.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "known_resources": [
                {
                    "key": item.key,
                    "location": item.location,
                    "required": item.required,
                    "resource_id": item.resource_id,
                    "resource_type": item.resource_type,
                }
                for item in self.known_resources
            ],
            "production_authority_sha256": self.production_authority_sha256,
            "provision_client_id": self.provision_client_id,
            "rehearsal_id": self.rehearsal_id,
            "requests": [
                {
                    "api_version": item.api_version,
                    "key": item.key,
                    "resource_id": item.resource_id,
                }
                for item in self.requests
            ],
            "runtime_policy_binding_sha256": self.runtime_policy_binding_sha256,
            "target_location": self.target_location,
            "target_resource_group_id": self.target_resource_group_id,
        }

    def _is_valid(self) -> bool:
        return (
            type(self) is _Plan
            and self._seal is _PLAN_SEAL
            and type(self.requests) is tuple
            and len(self.requests) == 4
            and all(type(item) is _Request for item in self.requests)
            and type(self.known_resources) is tuple
            and len(self.known_resources) == 7
            and all(type(item) is _KnownResource for item in self.known_resources)
            and self.sha256 == content._digest(content._canonical(self._document()))
        )


def _derive_plan(
    authority: database.ControllerReceiptV2DatabaseObservationAuthority,
    *,
    policy: Mapping[str, Any],
    now: datetime,
) -> _Plan:
    try:
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
            or now.microsecond != 0
            or type(authority)
            is not database.ControllerReceiptV2DatabaseObservationAuthority
            or not authority._is_authenticated()
            or authority.observed_at > now
            or now - authority.observed_at
            > timedelta(
                seconds=policy["observation"]["freshness"][
                    "maximum_database_observation_age_seconds"
                ]
            )
        ):
            _fail("controller_receipt_isolation.authority_invalid")
        production_authority = authority._production_authority
        arm_observation = authority._arm_observation
        database_plan = database._derive_plan(
            production_authority,
            arm_observation,
            policy=database.load_controller_receipt_v2_database_observation_policy(),
            now=now,
        )
        runtime, controller, _production_policy, production_document = (
            arm._fresh_context(production_authority, now)
        )
        database_document = authority.authority
        arm_document = arm_observation.authority
        target = arm_document["target"]
        fixed = controller["fixed_bindings"]
        target_group = fixed["target_resource_group_id"]
        target_location = production_document["request"]["location"]
        resources = {
            "target_postgres_server": target["target_postgres_server_id"],
            "target_vnet": target["target_vnet_id"],
            "target_private_endpoint": target["target_private_endpoint_id"],
            "target_network_interface": target["target_network_interface_id"],
            "target_private_dns_zone": target["target_private_dns_zone_id"],
            "target_private_dns_link": target["target_private_dns_link_id"],
            "target_private_dns_zone_group": target["target_private_dns_zone_group_id"],
        }
        resource_types = {
            "target_postgres_server": "Microsoft.DBforPostgreSQL/flexibleServers",
            "target_vnet": "Microsoft.Network/virtualNetworks",
            "target_private_endpoint": "Microsoft.Network/privateEndpoints",
            "target_network_interface": "Microsoft.Network/networkInterfaces",
            "target_private_dns_zone": "Microsoft.Network/privateDnsZones",
            "target_private_dns_link": (
                "Microsoft.Network/privateDnsZones/virtualNetworkLinks"
            ),
            "target_private_dns_zone_group": (
                "Microsoft.Network/privateEndpoints/privateDnsZoneGroups"
            ),
        }
        required = tuple(
            policy["observation"]["inventory"]["required_top_level_resource_keys"]
        )
        optional = tuple(
            policy["observation"]["inventory"]["optional_nested_resource_keys"]
        )
        ordered = (*required, *optional)
        if (
            database_document["database_observation_policy_sha256"]
            != f"sha256:{database.POLICY_SHA256}"
            or database_document["arm_observation_sha256"]
            != content._digest(arm_observation.authority_bytes)
            or database_document["production_authority_sha256"]
            != content._digest(production_authority.authority_bytes)
            or database_document["plan_sha256"] != database_plan.sha256
            or database_document["claims"]["database_role_authenticated"] is not True
            or arm_document["claims"]["arm_network_facts_authenticated"] is not True
            or set(resources) != set(ordered)
            or len({value.lower() for value in resources.values()}) != len(resources)
            or not content._resource_group_id(target_group)
            or any(
                not content._azure_id(resource_id)
                or content._resource_group(resource_id).lower() != target_group.lower()
                or content._resource_type(resource_id) != resource_types[key].lower()
                for key, resource_id in resources.items()
            )
            or not now < database_plan.expires_at
        ):
            _fail("controller_receipt_isolation.plan_invalid")
        api = policy["observation"]["api_versions"]
        requests = (
            _Request(
                "firewall_rules",
                f"{resources['target_postgres_server']}/firewallRules",
                api["firewall_rules"],
            ),
            _Request(
                "vnet_peerings",
                f"{resources['target_vnet']}/virtualNetworkPeerings",
                api["vnet_peerings"],
            ),
            _Request(
                "public_ip_addresses",
                f"{target_group}/providers/Microsoft.Network/publicIPAddresses",
                api["public_ip_addresses"],
            ),
            _Request(
                "resource_group_inventory",
                f"{target_group}/resources",
                api["resource_group_inventory"],
            ),
        )
        known = tuple(
            _KnownResource(
                key,
                resources[key],
                resource_types[key],
                (
                    "global"
                    if key in {"target_private_dns_zone", "target_private_dns_link"}
                    else target_location
                ),
                key in required,
            )
            for key in ordered
        )
        fields: dict[str, Any] = {
            "requests": requests,
            "known_resources": known,
            "provision_client_id": production_document["identity"][
                "provision_job_uami_client_id"
            ],
            "target_resource_group_id": target_group,
            "target_location": target_location,
            "rehearsal_id": authority.rehearsal_id,
            "execution_resource_id": authority.execution_resource_id,
            "expires_at": database_plan.expires_at,
            "database_observation_sha256": content._digest(authority.authority_bytes),
            "arm_observation_sha256": content._digest(arm_observation.authority_bytes),
            "production_authority_sha256": content._digest(
                production_authority.authority_bytes
            ),
            "controller_policy_binding_sha256": content._policy_binding_digest(
                controller
            ),
            "runtime_policy_binding_sha256": runtime_binding._policy_binding_digest(
                runtime
            ),
        }
        value = object.__new__(_Plan)
        for name, item in fields.items():
            object.__setattr__(value, name, item)
        object.__setattr__(
            value,
            "sha256",
            content._digest(content._canonical(value._document())),
        )
        object.__setattr__(value, "_seal", _PLAN_SEAL)
        if not value._is_valid():
            _fail("controller_receipt_isolation.plan_invalid")
        return value
    except ControllerReceiptV2IsolationInventoryFailure:
        raise
    except Exception:
        _fail("controller_receipt_isolation.plan_invalid")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _AuthenticatedResponse:
    key: str
    resource_id: str
    api_version: str
    body: bytes = dataclass_field(repr=False)
    body_sha256: str
    observed_at: datetime
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_isolation.response_invalid")

    def _is_authenticated_for(self, request: _Request) -> bool:
        return (
            type(self) is _AuthenticatedResponse
            and self._seal is _RESPONSE_SEAL
            and self.key == request.key
            and self.resource_id == request.resource_id
            and self.api_version == request.api_version
            and type(self.body) is bytes
            and self.body_sha256 == content._digest(self.body)
            and type(self.observed_at) is datetime
            and self.observed_at.tzinfo is not None
            and self.observed_at.utcoffset() == timedelta(0)
            and self.observed_at.microsecond == 0
        )


def _collection(raw: bytes) -> list[Any]:
    try:
        document = adapters._load_mapping(
            raw, "controller_receipt_isolation.response_invalid"
        )
    except Exception:
        _fail("controller_receipt_isolation.response_invalid")
    if set(document) != {"value"} or type(document["value"]) is not list:
        _fail("controller_receipt_isolation.response_invalid")
    return document["value"]


def _hierarchical_name(resource_id: str) -> str:
    parts = resource_id.split("/")
    try:
        index = next(
            offset for offset, item in enumerate(parts) if item.lower() == "providers"
        )
    except StopIteration:
        _fail("controller_receipt_isolation.inventory_invalid")
    provider = parts[index + 1 :]
    if len(provider) < 3 or len(provider) % 2 != 1:
        _fail("controller_receipt_isolation.inventory_invalid")
    return "/".join(provider[2::2])


def _inventory_projection(
    values: list[Any], plan: _Plan, policy: Mapping[str, Any]
) -> tuple[str, int, int, int]:
    expected = {item.resource_id.lower(): item for item in plan.known_resources}
    required = {
        item.resource_id.lower() for item in plan.known_resources if item.required
    }
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    storage = {
        value.lower()
        for value in policy["observation"]["inventory"]["storage_resource_types"]
    }
    redis = {
        value.lower()
        for value in policy["observation"]["inventory"]["redis_resource_types"]
    }
    storage_count = 0
    redis_count = 0
    unknown_count = 0
    for value in values:
        if not isinstance(value, Mapping):
            _fail("controller_receipt_isolation.inventory_invalid")
        resource_id = value.get("id")
        resource_type = value.get("type")
        name = value.get("name")
        location = value.get("location")
        if (
            type(resource_id) is not str
            or not content._azure_id(resource_id)
            or type(resource_type) is not str
            or type(name) is not str
            or type(location) is not str
            or not all(0x21 <= ord(character) <= 0x7E for character in resource_type)
            or not all(0x20 <= ord(character) <= 0x7E for character in name)
            or not all(0x21 <= ord(character) <= 0x7E for character in location)
            or content._resource_group(resource_id).lower()
            != plan.target_resource_group_id.lower()
            or content._resource_type(resource_id) != resource_type.lower()
            or _hierarchical_name(resource_id).lower() != name.lower()
        ):
            _fail("controller_receipt_isolation.inventory_invalid")
        lowered_id = resource_id.lower()
        lowered_type = resource_type.lower()
        if lowered_id in seen:
            _fail("controller_receipt_isolation.inventory_invalid")
        seen.add(lowered_id)
        known = expected.get(lowered_id)
        if known is not None:
            if (
                lowered_type != known.resource_type.lower()
                or location.lower() != known.location.lower()
            ):
                _fail("controller_receipt_isolation.inventory_invalid")
        elif lowered_type in storage:
            storage_count += 1
        elif lowered_type in redis:
            redis_count += 1
        else:
            unknown_count += 1
        normalized.append(
            {
                "id": lowered_id,
                "location": location.lower(),
                "name": name.lower(),
                "type": lowered_type,
            }
        )
    if not required.issubset(seen):
        _fail("controller_receipt_isolation.inventory_invalid")
    digest = content._digest(
        content._canonical(sorted(normalized, key=lambda item: item["id"]))
    )
    return digest, storage_count, redis_count, unknown_count


def _fact_document(
    responses: tuple[_AuthenticatedResponse, ...],
    plan: _Plan,
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if (
        type(responses) is not tuple
        or len(responses) != len(plan.requests)
        or any(
            not response._is_authenticated_for(request)
            for response, request in zip(responses, plan.requests, strict=True)
        )
    ):
        _fail("controller_receipt_isolation.response_invalid")
    collections = {response.key: _collection(response.body) for response in responses}
    firewall_count = len(collections["firewall_rules"])
    peering_count = len(collections["vnet_peerings"])
    public_ip_count = len(collections["public_ip_addresses"])
    inventory_sha256, storage_count, redis_count, unknown_count = _inventory_projection(
        collections["resource_group_inventory"], plan, policy
    )
    counts = (
        firewall_count,
        peering_count,
        public_ip_count,
        storage_count,
        redis_count,
        unknown_count,
    )
    if any(type(value) is not int or value != 0 for value in counts):
        _fail("controller_receipt_isolation.isolation_present")
    facts = {
        "public_access": "Disabled",
        "firewall_rule_count": firewall_count,
        "vnet_peering_count": peering_count,
        "public_ip_count": public_ip_count,
        "unknown_resource_count": unknown_count,
        "target_resource_group_storage_account_count": storage_count,
        "target_resource_group_redis_resource_count": redis_count,
        "known_resource_inventory_sha256": inventory_sha256,
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
    return facts, manifest


def _validate_fact_document(value: Any) -> dict[str, Any]:
    try:
        document = json.loads(content._canonical(value))
    except Exception:
        _fail("controller_receipt_isolation.inventory_invalid")
    count_fields = (
        "firewall_rule_count",
        "vnet_peering_count",
        "public_ip_count",
        "unknown_resource_count",
        "target_resource_group_storage_account_count",
        "target_resource_group_redis_resource_count",
    )
    if (
        set(document)
        != {
            "public_access",
            "known_resource_inventory_sha256",
            *count_fields,
        }
        or document["public_access"] != "Disabled"
        or type(document["known_resource_inventory_sha256"]) is not str
        or content.DIGEST_RE.fullmatch(document["known_resource_inventory_sha256"])
        is None
        or any(
            type(document[name]) is not int or document[name] != 0
            for name in count_fields
        )
    ):
        _fail("controller_receipt_isolation.inventory_invalid")
    return document


def _authority_document(
    *,
    policy: Mapping[str, Any],
    plan: _Plan,
    responses: tuple[_AuthenticatedResponse, ...],
    started_at: datetime,
    observed_at: datetime,
) -> dict[str, Any]:
    facts, manifest = _fact_document(responses, plan, policy)
    facts = _validate_fact_document(facts)
    maximum_window = policy["observation"]["maximum_observation_window_seconds"]
    if (
        type(started_at) is not datetime
        or type(observed_at) is not datetime
        or started_at.tzinfo is None
        or observed_at.tzinfo is None
        or started_at.utcoffset() != timedelta(0)
        or observed_at.utcoffset() != timedelta(0)
        or started_at.microsecond != 0
        or observed_at.microsecond != 0
        or not started_at <= observed_at
        or observed_at - started_at > timedelta(seconds=maximum_window)
        or any(
            not started_at <= response.observed_at <= observed_at
            for response in responses
        )
    ):
        _fail("controller_receipt_isolation.clock_invalid")
    lifetime = policy["observation"]["freshness"]["maximum_output_lifetime_seconds"]
    expires_at = min(plan.expires_at, observed_at + timedelta(seconds=lifetime))
    return {
        "schema_version": 1,
        "kind": policy["output"]["kind"],
        "isolation_inventory_policy_id": policy["policy_id"],
        "isolation_inventory_policy_sha256": f"sha256:{POLICY_SHA256}",
        "controller_policy_binding_sha256": plan.controller_policy_binding_sha256,
        "runtime_policy_binding_sha256": plan.runtime_policy_binding_sha256,
        "production_authority_sha256": plan.production_authority_sha256,
        "arm_observation_sha256": plan.arm_observation_sha256,
        "database_observation_sha256": plan.database_observation_sha256,
        "rehearsal_id": plan.rehearsal_id,
        "execution_resource_id": plan.execution_resource_id,
        "target_resource_group_id": plan.target_resource_group_id,
        "request_plan_sha256": plan.sha256,
        "response_manifest": manifest,
        "response_manifest_sha256": content._digest(content._canonical(manifest)),
        "facts": facts,
        "observation_started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "claims": dict(policy["claims"]),
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ControllerReceiptV2IsolationInventoryAuthority:
    """Sealed point-in-time isolation counts, not an execution-history proof."""

    authority_bytes: bytes = dataclass_field(repr=False)
    rehearsal_id: str
    execution_resource_id: str
    observed_at: datetime
    point_in_time_isolation_inventory_authenticated: bool
    public_access_authenticated: bool
    provider_facts_authenticated: bool
    execution_history_authenticated: bool
    controller_receipt_exists: bool
    runtime_authority: bool
    executable: bool
    _database_observation: database.ControllerReceiptV2DatabaseObservationAuthority = (
        dataclass_field(repr=False, compare=False)
    )
    _policy_bytes: bytes = dataclass_field(repr=False, compare=False)
    _responses: tuple[_AuthenticatedResponse, ...] = dataclass_field(
        repr=False, compare=False
    )
    _started_at: datetime = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_isolation.authority_unsealed")

    @property
    def authority(self) -> dict[str, Any]:
        if not self._is_authenticated():
            _fail("controller_receipt_isolation.authority_invalid")
        return content._parse_canonical(self.authority_bytes, MAXIMUM_POLICY_BYTES)

    def _is_authenticated(self) -> bool:
        try:
            policy = validate_controller_receipt_v2_isolation_inventory_policy(
                content._parse_canonical(self._policy_bytes, MAXIMUM_POLICY_BYTES)
            )
            plan = _derive_plan(
                self._database_observation, policy=policy, now=self.observed_at
            )
            expected = _authority_document(
                policy=policy,
                plan=plan,
                responses=self._responses,
                started_at=self._started_at,
                observed_at=self.observed_at,
            )
            return (
                type(self) is ControllerReceiptV2IsolationInventoryAuthority
                and self._seal is _AUTHORITY_SEAL
                and self.authority_bytes == content._canonical(expected)
                and self.rehearsal_id == plan.rehearsal_id
                and self.execution_resource_id == plan.execution_resource_id
                and self.point_in_time_isolation_inventory_authenticated is True
                and self.public_access_authenticated is True
                and self.provider_facts_authenticated is False
                and self.execution_history_authenticated is False
                and self.controller_receipt_exists is False
                and self.runtime_authority is False
                and self.executable is False
            )
        except Exception:
            return False


def _mint_authority(
    *,
    database_observation: database.ControllerReceiptV2DatabaseObservationAuthority,
    policy: Mapping[str, Any],
    plan: _Plan,
    responses: tuple[_AuthenticatedResponse, ...],
    started_at: datetime,
    observed_at: datetime,
) -> ControllerReceiptV2IsolationInventoryAuthority:
    document = _authority_document(
        policy=policy,
        plan=plan,
        responses=responses,
        started_at=started_at,
        observed_at=observed_at,
    )
    value = object.__new__(ControllerReceiptV2IsolationInventoryAuthority)
    fields = {
        "authority_bytes": content._canonical(document),
        "rehearsal_id": plan.rehearsal_id,
        "execution_resource_id": plan.execution_resource_id,
        "observed_at": observed_at,
        "point_in_time_isolation_inventory_authenticated": True,
        "public_access_authenticated": True,
        "provider_facts_authenticated": False,
        "execution_history_authenticated": False,
        "controller_receipt_exists": False,
        "runtime_authority": False,
        "executable": False,
        "_database_observation": database_observation,
        "_policy_bytes": content._canonical(policy),
        "_responses": responses,
        "_started_at": started_at,
        "_seal": _AUTHORITY_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    if not value._is_authenticated():
        _fail("controller_receipt_isolation.authority_invalid")
    return value


class ManagedIdentityControllerReceiptV2IsolationInventoryObserver:
    """One-shot four-GET observer whose public call has one sealed input."""

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
    _arm_opener: adapters._HttpOpener
    _clock: Callable[[], datetime]
    _deadline_factory: Callable[[int], Any]
    _epoch_clock: Callable[[], float]
    _identity_opener: adapters._HttpOpener
    _lock: threading.Lock
    _monotonic: Callable[[], float]
    _used: bool

    def __init__(self) -> None:
        _fail("controller_receipt_isolation.configuration_invalid")

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
    ) -> ManagedIdentityControllerReceiptV2IsolationInventoryObserver:
        if (
            cls is not ManagedIdentityControllerReceiptV2IsolationInventoryObserver
            or not all(
                callable(item)
                for item in (clock, epoch_clock, monotonic, deadline_factory)
            )
            or not all(
                callable(getattr(item, "open", None))
                for item in (identity_opener, arm_opener)
            )
        ):
            _fail("controller_receipt_isolation.configuration_invalid")
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
        database_observation: database.ControllerReceiptV2DatabaseObservationAuthority,
    ) -> ControllerReceiptV2IsolationInventoryAuthority:
        try:
            return self._observe(database_observation)
        except Exception as error:
            code = (
                error.args[0]
                if type(error) is ControllerReceiptV2IsolationInventoryFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
                else "controller_receipt_isolation.observation_failed"
            )
        raise ControllerReceiptV2IsolationInventoryFailure(code) from None

    def _observe(
        self,
        database_observation: database.ControllerReceiptV2DatabaseObservationAuthority,
    ) -> ControllerReceiptV2IsolationInventoryAuthority:
        with self._lock:
            if self._used:
                _fail("controller_receipt_isolation.already_used")
            self._used = True
        policy = load_controller_receipt_v2_isolation_inventory_policy()
        trusted_utc = ingress._TrustedUtcTimeline(self._clock)
        timeline = _Timeline(self._monotonic)
        started_at = trusted_utc.read()
        deadline = (
            timeline.read()
            + policy["observation"]["maximum_observation_window_seconds"]
        )
        plan = _derive_plan(database_observation, policy=policy, now=started_at)

        def checkpoint() -> datetime:
            now = trusted_utc.read()
            current = _derive_plan(database_observation, policy=policy, now=now)
            if current != plan or timeline.read() > deadline:
                _fail("controller_receipt_isolation.authority_invalid")
            return now

        transport = policy["transport"]
        checkpoint()
        try:
            if (
                _remaining(timeline, deadline, transport["request_timeout_seconds"])
                != transport["request_timeout_seconds"]
            ):
                _fail("controller_receipt_isolation.deadline")
            token_source = adapters.ManagedIdentityTokenSource.from_environment(
                client_id=plan.provision_client_id,
                opener=self._identity_opener,
                clock=self._epoch_clock,
            )
            token = token_source.get_token(
                resource=transport["oauth_resource"],
                timeout_seconds=transport["request_timeout_seconds"],
            )
        except ControllerReceiptV2IsolationInventoryFailure:
            raise
        except Exception:
            _fail("controller_receipt_isolation.identity_failed")
        checkpoint()
        responses: list[_AuthenticatedResponse] = []
        try:
            for request_item in plan.requests:
                checkpoint()
                query = urllib.parse.urlencode(
                    {"api-version": request_item.api_version}
                )
                url = (
                    f"{transport['management_origin']}"
                    f"{request_item.resource_id}?{query}"
                )
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
                    with self._deadline_factory(
                        _remaining(
                            timeline,
                            deadline,
                            transport["request_timeout_seconds"],
                        )
                    ):
                        with self._arm_opener.open(
                            request, timeout=transport["request_timeout_seconds"]
                        ) as response:
                            status, _media, raw = adapters._read_json_response(
                                response,
                                expected_url=url,
                                maximum_bytes=transport["maximum_response_bytes"],
                            )
                except urllib.error.HTTPError as error:
                    if 300 <= int(error.code) < 400:
                        _fail("controller_receipt_isolation.redirect_refused")
                    _fail("controller_receipt_isolation.provider_refused")
                except ControllerReceiptV2IsolationInventoryFailure:
                    raise
                except Exception:
                    _fail("controller_receipt_isolation.provider_unavailable")
                if status != 200:
                    _fail("controller_receipt_isolation.provider_refused")
                _collection(raw)
                response_time = checkpoint()
                item = object.__new__(_AuthenticatedResponse)
                for name, value in {
                    "key": request_item.key,
                    "resource_id": request_item.resource_id,
                    "api_version": request_item.api_version,
                    "body": bytes(raw),
                    "body_sha256": content._digest(raw),
                    "observed_at": response_time,
                    "_seal": _RESPONSE_SEAL,
                }.items():
                    object.__setattr__(item, name, value)
                responses.append(item)
            observed_at = checkpoint()
            return _mint_authority(
                database_observation=database_observation,
                policy=policy,
                plan=plan,
                responses=tuple(responses),
                started_at=started_at,
                observed_at=observed_at,
            )
        finally:
            token = ""


def build_controller_receipt_v2_isolation_inventory_observer() -> NoReturn:
    load_controller_receipt_v2_isolation_inventory_policy(executable=True)
    _fail("controller_receipt_isolation.policy_disabled")


if __name__ == "__main__":
    load_controller_receipt_v2_isolation_inventory_policy(
        executable="--executable" in sys.argv
    )
    print("controller receipt v2 isolation inventory policy valid (dormant)")
