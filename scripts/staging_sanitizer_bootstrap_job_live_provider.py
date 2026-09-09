#!/usr/bin/env python3
"""Dormant one-shot ARM reader for the sanitizer bootstrap job boundary.

The transport can issue only a sealed, policy-derived sequence of exact GETs.
No public construction path exists until later reviewed slices authenticate the
runtime binding, renewed bootstrap identity, image, and semantic observer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, SupportsIndex

try:
    from scripts import staging_restore_runtime_adapters as adapters
    from scripts import (
        staging_restore_sanitizer_bootstrap_job_attachment_contract as attachment,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_runtime_adapters as adapters
    import staging_restore_sanitizer_bootstrap_job_attachment_contract as attachment


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-bootstrap-job-live-provider-policy.json"
)
POLICY_SHA256 = "13b0ee5ee2fca44ac54da442927eed22358eeff08bc165112f93820c89a33d65"
ATTACHMENT_CONTRACT_SHA256 = (
    "9ee7cae5d675ea8727e09254c1d0a5396c043994a54c39d4b50290a168f088d2"
)
ACTIVATION_FAILURE = "activation.sanitizer_bootstrap_job_live_provider_unavailable"
MAXIMUM_POLICY_BYTES = 64 * 1024
SUBSCRIPTION_ID = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
CONTROL_RESOURCE_GROUP = "cosmaneura-staging-rehearsal-control"
IDENTITY_NAME = "cosmaneura-staging-restore-bootstrap-job"
JOB_NAME = "cosmaneura-stg-restore-bootstrap"
SUBSCRIPTION_SCOPE = f"/subscriptions/{SUBSCRIPTION_ID}"
CONTROL_RESOURCE_GROUP_ID = (
    f"{SUBSCRIPTION_SCOPE}/resourceGroups/{CONTROL_RESOURCE_GROUP}"
)
IDENTITY_RESOURCE_ID = (
    f"{CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
    f"userAssignedIdentities/{IDENTITY_NAME}"
)
JOB_RESOURCE_ID = f"{CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.App/jobs/{JOB_NAME}"
EXECUTIONS_RESOURCE_ID = f"{JOB_RESOURCE_ID}/executions"
IDENTITY_API_VERSION = "2023-01-31"
JOB_API_VERSION = "2024-03-01"
ROLE_ASSIGNMENT_API_VERSION = "2022-04-01"
PHASES = ("initial_absence", "present", "final_absence")
REGISTRY_ID_RE = re.compile(
    rf"^/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/"
    r"[A-Za-z0-9._()-]{0,89}[A-Za-z0-9_()-]/"
    r"providers/Microsoft\.ContainerRegistry/"
    r"registries/[a-z0-9]{5,50}$"
)
_PLAN_SEAL = object()
_REQUEST_SEAL = object()
_RESPONSE_SEAL = object()

CLAIMS = dict.fromkeys(
    (
        "bootstrap_job_configuration_authenticated",
        "bootstrap_job_execution_authenticated",
        "bootstrap_job_identity_attachment_authenticated",
        "bootstrap_identity_current_live_state_authenticated",
        "bootstrap_authority_authenticated",
        "database_connection_authority",
        "database_mutation_authority",
        "provider_mutation_authority",
        "runtime_authority",
        "sanitizer_authority",
        "workflow_integrated",
        "executable",
    ),
    False,
)
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "bootstrap_job_live_provider.already_used",
        "bootstrap_job_live_provider.clock_invalid",
        "bootstrap_job_live_provider.deadline",
        "bootstrap_job_live_provider.identity_failed",
        "bootstrap_job_live_provider.plan_invalid",
        "bootstrap_job_live_provider.policy_invalid",
        "bootstrap_job_live_provider.provider_refused",
        "bootstrap_job_live_provider.provider_unavailable",
        "bootstrap_job_live_provider.redirect_refused",
        "bootstrap_job_live_provider.response_invalid",
        "bootstrap_job_live_provider.response_too_large",
        "bootstrap_job_live_provider.transport_invalid",
    }
)


class SanitizerBootstrapJobLiveProviderFailure(RuntimeError):
    """Bounded refusal without provider, identity, or response content."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "bootstrap_job_live_provider.response_invalid"
    raise SanitizerBootstrapJobLiveProviderFailure(code) from None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("bootstrap_job_live_provider.policy_invalid")


def _parse_policy(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAXIMUM_POLICY_BYTES:
        _fail("bootstrap_job_live_provider.policy_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("bootstrap_job_live_provider.policy_invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "bootstrap_job_live_provider.policy_invalid"
            ),
            parse_float=lambda _value: _fail(
                "bootstrap_job_live_provider.policy_invalid"
            ),
        )
        attachment._validate_exact_json_tree(document)  # noqa: SLF001
    except SanitizerBootstrapJobLiveProviderFailure:
        raise
    except Exception:
        _fail("bootstrap_job_live_provider.policy_invalid")
    if type(document) is not dict:
        _fail("bootstrap_job_live_provider.policy_invalid")
    return document


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
            _fail("bootstrap_job_live_provider.policy_invalid")
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
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), POLICY_SHA256)
        ):
            _fail("bootstrap_job_live_provider.policy_invalid")
    except SanitizerBootstrapJobLiveProviderFailure:
        raise
    except OSError:
        _fail("bootstrap_job_live_provider.policy_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _parse_policy(raw)


def _expected_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy_id": "cosmaneura-staging-sanitizer-bootstrap-job-live-provider-v1",
        "closed_world": True,
        "upstream_policy": {
            "policy_id": "cosmaneura-staging-sanitizer-bootstrap-job-attachment-envelope-v1",
            "sha256": f"sha256:{ATTACHMENT_CONTRACT_SHA256}",
        },
        "transport": {
            "management_origin": adapters.ARM_ORIGIN,
            "oauth_resource": adapters.ARM_RESOURCE,
            "method": "GET",
            "request_timeout_seconds": adapters.REQUEST_TIMEOUT_SECONDS,
            "maximum_elapsed_seconds": 150,
            "maximum_response_bytes": adapters.MAXIMUM_PROVIDER_BYTES,
            "maximum_token_requests": 1,
            "maximum_arm_get_requests_per_plan": 4,
            "accepted_statuses": [200, 404],
            "ambient_credentials_permitted": False,
            "ambient_proxy_permitted": False,
            "caller_body_headers_method_query_or_url_permitted": False,
            "disk_materialization_permitted": False,
            "polling_permitted": False,
            "redirects_permitted": False,
            "retries_permitted": False,
            "raw_provider_body_public": False,
        },
        "resources": {
            "subscription_id": SUBSCRIPTION_ID,
            "control_resource_group": CONTROL_RESOURCE_GROUP,
            "bootstrap_identity_name": IDENTITY_NAME,
            "bootstrap_job_name": JOB_NAME,
            "bootstrap_identity_api_version": IDENTITY_API_VERSION,
            "bootstrap_job_api_version": JOB_API_VERSION,
            "private_registry_acrpull_role_assignment_api_version": ROLE_ASSIGNMENT_API_VERSION,
            "private_registry_resource_id_source": "future_authenticated_restore_runtime_binding.private_registry.resource_id",
            "private_registry_acrpull_role_assignment_name_source": "future_create_only_control_plane.provider_observed_name",
            "bootstrap_identity_client_id_source": "future_authenticated_bootstrap_identity.client_id",
        },
        "plans": {
            "initial_absence": [
                {"key": "bootstrap_job", "expected_status": 404},
                {"key": "bootstrap_identity", "expected_status": 404},
                {
                    "key": "private_registry_acrpull_role_assignment",
                    "expected_status": 404,
                },
            ],
            "present": [
                {"key": "bootstrap_identity", "expected_status": 200},
                {
                    "key": "private_registry_acrpull_role_assignment",
                    "expected_status": 200,
                },
                {"key": "bootstrap_job", "expected_status": 200},
                {"key": "bootstrap_job_executions", "expected_status": 200},
            ],
            "final_absence": [
                {"key": "bootstrap_job", "expected_status": 404},
                {
                    "key": "private_registry_acrpull_role_assignment",
                    "expected_status": 404,
                },
                {"key": "bootstrap_identity", "expected_status": 404},
            ],
        },
        "claims": CLAIMS,
        "status": {
            "sealed_request_plan_implemented": True,
            "one_shot_get_transport_implemented": True,
            "bootstrap_job_semantic_observer_implemented": False,
            "bootstrap_job_attachment_authenticator_implemented": False,
            "live_provider_calls_enabled": False,
            "workflow_integrated": False,
            "executable": False,
            "activation_ready": False,
            "public_activation_failure": ACTIVATION_FAILURE,
            "activation_blockers": [
                "authenticated_runtime_binding_unavailable",
                "authenticated_bootstrap_identity_unavailable",
                "authenticated_bootstrap_image_unavailable",
                "bootstrap_job_semantic_observer_unimplemented",
                "bootstrap_job_attachment_authenticator_unimplemented",
                "bootstrap_control_plane_adapter_unimplemented",
                "workflow_integration_unimplemented",
            ],
        },
    }


def validate_bootstrap_job_live_provider_policy(
    policy: Any, *, executable: bool = False
) -> dict[str, Any]:
    if type(policy) is not dict or type(executable) is not bool:
        _fail("bootstrap_job_live_provider.policy_invalid")
    template = _read_policy()
    try:
        attachment._validate_exact_json_tree(policy)  # noqa: SLF001
        document = _parse_policy(_canonical(policy))
    except SanitizerBootstrapJobLiveProviderFailure:
        raise
    except Exception:
        _fail("bootstrap_job_live_provider.policy_invalid")
    if (
        attachment.CONTRACT_SHA256 != ATTACHMENT_CONTRACT_SHA256
        or not hmac.compare_digest(_canonical(document), _canonical(template))
        or document != _expected_policy()
    ):
        _fail("bootstrap_job_live_provider.policy_invalid")
    if executable:
        _fail(ACTIVATION_FAILURE)
    return document


def load_bootstrap_job_live_provider_policy(
    *, executable: bool = False
) -> dict[str, Any]:
    return validate_bootstrap_job_live_provider_policy(
        _read_policy(), executable=executable
    )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _TransportConfiguration:
    management_origin: str
    oauth_resource: str
    request_timeout_seconds: int
    maximum_elapsed_seconds: int
    maximum_response_bytes: int

    def __init__(self) -> None:
        _fail("bootstrap_job_live_provider.transport_invalid")


def _compile_transport_configuration(
    policy: Mapping[str, Any],
) -> _TransportConfiguration:
    validated = validate_bootstrap_job_live_provider_policy(dict(policy))
    transport = validated["transport"]
    value = object.__new__(_TransportConfiguration)
    for name, item in {
        "management_origin": transport["management_origin"],
        "oauth_resource": transport["oauth_resource"],
        "request_timeout_seconds": transport["request_timeout_seconds"],
        "maximum_elapsed_seconds": transport["maximum_elapsed_seconds"],
        "maximum_response_bytes": transport["maximum_response_bytes"],
    }.items():
        object.__setattr__(value, name, item)
    return value


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _ArmReadRequest:
    key: str
    resource_id: str
    api_version: str
    expected_status: int
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("bootstrap_job_live_provider.plan_invalid")

    def _is_valid(self) -> bool:
        return (
            type(self) is _ArmReadRequest
            and self._seal is _REQUEST_SEAL
            and type(self.key) is str
            and type(self.resource_id) is str
            and type(self.api_version) is str
            and type(self.expected_status) is int
            and self.expected_status in {200, 404}
        )


def _role_assignment_resource_id(registry_resource_id: str, name: str) -> str:
    return (
        f"{registry_resource_id}/providers/Microsoft.Authorization/"
        f"roleAssignments/{name}"
    )


def _request(
    key: str, resource_id: str, api_version: str, status_code: int
) -> _ArmReadRequest:
    value = object.__new__(_ArmReadRequest)
    for name, item in {
        "key": key,
        "resource_id": resource_id,
        "api_version": api_version,
        "expected_status": status_code,
        "_seal": _REQUEST_SEAL,
    }.items():
        object.__setattr__(value, name, item)
    if not value._is_valid():
        _fail("bootstrap_job_live_provider.plan_invalid")
    return value


def _expected_requests(
    phase: str, registry_resource_id: str, role_assignment_name: str
) -> tuple[_ArmReadRequest, ...]:
    role_assignment_id = _role_assignment_resource_id(
        registry_resource_id, role_assignment_name
    )
    resources = {
        "bootstrap_identity": (IDENTITY_RESOURCE_ID, IDENTITY_API_VERSION),
        "bootstrap_job": (JOB_RESOURCE_ID, JOB_API_VERSION),
        "bootstrap_job_executions": (EXECUTIONS_RESOURCE_ID, JOB_API_VERSION),
        "private_registry_acrpull_role_assignment": (
            role_assignment_id,
            ROLE_ASSIGNMENT_API_VERSION,
        ),
    }
    policy = load_bootstrap_job_live_provider_policy()
    try:
        sequence = policy["plans"][phase]
    except (KeyError, TypeError):
        _fail("bootstrap_job_live_provider.plan_invalid")
    return tuple(
        _request(
            item["key"],
            resources[item["key"]][0],
            resources[item["key"]][1],
            item["expected_status"],
        )
        for item in sequence
    )


def _plan_material(
    *,
    phase: str,
    identity_client_id: str,
    registry_resource_id: str,
    role_assignment_name: str,
    requests: tuple[_ArmReadRequest, ...],
) -> dict[str, Any]:
    return {
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "phase": phase,
        "identity_client_id": identity_client_id,
        "private_registry_resource_id": registry_resource_id,
        "private_registry_acrpull_role_assignment_name": role_assignment_name,
        "requests": [
            {
                "key": request.key,
                "resource_id": request.resource_id,
                "api_version": request.api_version,
                "expected_status": request.expected_status,
            }
            for request in requests
        ],
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class BootstrapJobLiveReadPlan:
    """Process-local exact GET sequence; not provider authority."""

    phase: str
    request_plan_sha256: str
    _identity_client_id: str = dataclass_field(repr=False)
    _registry_resource_id: str = dataclass_field(repr=False)
    _role_assignment_name: str = dataclass_field(repr=False)
    _requests: tuple[_ArmReadRequest, ...] = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("bootstrap_job_live_provider.plan_invalid")

    def __copy__(self) -> NoReturn:
        _fail("bootstrap_job_live_provider.plan_invalid")

    def __deepcopy__(self, _memo: Any) -> NoReturn:
        _fail("bootstrap_job_live_provider.plan_invalid")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        _fail("bootstrap_job_live_provider.plan_invalid")

    def _is_valid(self) -> bool:
        try:
            expected = _expected_requests(
                self.phase,
                self._registry_resource_id,
                self._role_assignment_name,
            )
            material = _plan_material(
                phase=self.phase,
                identity_client_id=self._identity_client_id,
                registry_resource_id=self._registry_resource_id,
                role_assignment_name=self._role_assignment_name,
                requests=self._requests,
            )
            return (
                type(self) is BootstrapJobLiveReadPlan
                and self._seal is _PLAN_SEAL
                and self.phase in PHASES
                and adapters.UUID_RE.fullmatch(self._identity_client_id) is not None
                and REGISTRY_ID_RE.fullmatch(self._registry_resource_id) is not None
                and adapters.UUID_RE.fullmatch(self._role_assignment_name) is not None
                and type(self._requests) is tuple
                and len(self._requests) <= 4
                and all(type(item) is _ArmReadRequest for item in self._requests)
                and all(item._is_valid() for item in self._requests)
                and self._requests == expected
                and self.request_plan_sha256
                == hashlib.sha256(_canonical(material)).hexdigest()
            )
        except Exception:
            return False


def _seal_reviewed_read_plan(
    *,
    phase: str,
    identity_client_id: str,
    private_registry_resource_id: str,
    private_registry_acrpull_role_assignment_name: str,
) -> BootstrapJobLiveReadPlan:
    """Private bridge for a later same-process semantic authenticator."""

    if (
        type(phase) is not str
        or phase not in PHASES
        or type(identity_client_id) is not str
        or adapters.UUID_RE.fullmatch(identity_client_id) is None
        or type(private_registry_resource_id) is not str
        or REGISTRY_ID_RE.fullmatch(private_registry_resource_id) is None
        or type(private_registry_acrpull_role_assignment_name) is not str
        or adapters.UUID_RE.fullmatch(private_registry_acrpull_role_assignment_name)
        is None
    ):
        _fail("bootstrap_job_live_provider.plan_invalid")
    requests = _expected_requests(
        phase,
        private_registry_resource_id,
        private_registry_acrpull_role_assignment_name,
    )
    material = _plan_material(
        phase=phase,
        identity_client_id=identity_client_id,
        registry_resource_id=private_registry_resource_id,
        role_assignment_name=private_registry_acrpull_role_assignment_name,
        requests=requests,
    )
    value = object.__new__(BootstrapJobLiveReadPlan)
    for name, item in {
        "phase": phase,
        "request_plan_sha256": hashlib.sha256(_canonical(material)).hexdigest(),
        "_identity_client_id": identity_client_id,
        "_registry_resource_id": private_registry_resource_id,
        "_role_assignment_name": private_registry_acrpull_role_assignment_name,
        "_requests": requests,
        "_seal": _PLAN_SEAL,
    }.items():
        object.__setattr__(value, name, item)
    if not value._is_valid():
        _fail("bootstrap_job_live_provider.plan_invalid")
    return value


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            or value.microsecond != 0
        ):
            _fail("bootstrap_job_live_provider.clock_invalid")
        normalized = value.astimezone(UTC)
    except SanitizerBootstrapJobLiveProviderFailure:
        raise
    except Exception:
        _fail("bootstrap_job_live_provider.clock_invalid")
    return normalized


def _monotonic_value(clock: Callable[[], float]) -> float:
    try:
        value = clock()
        if (
            type(value) not in {int, float}
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
        ):
            _fail("bootstrap_job_live_provider.clock_invalid")
        normalized = float(value)
    except SanitizerBootstrapJobLiveProviderFailure:
        raise
    except Exception:
        _fail("bootstrap_job_live_provider.clock_invalid")
    return normalized


class AuthenticatedBootstrapJobArmRead:
    """Authenticated metadata for one exact read; response bytes are discarded."""

    __slots__ = (
        "_api_version",
        "_request_plan_sha256",
        "_resource_id",
        "_seal",
        "_validator",
        "body_sha256",
        "key",
        "observed_at",
        "status",
    )
    _api_version: str
    _request_plan_sha256: str
    _resource_id: str
    _seal: object
    _validator: Callable[[AuthenticatedBootstrapJobArmRead], bool]
    body_sha256: str
    key: str
    observed_at: datetime
    status: int

    def __init__(self) -> None:
        _fail("bootstrap_job_live_provider.response_invalid")

    def __setattr__(self, _name: str, _value: Any) -> NoReturn:
        _fail("bootstrap_job_live_provider.response_invalid")

    def __delattr__(self, _name: str) -> NoReturn:
        _fail("bootstrap_job_live_provider.response_invalid")

    def __copy__(self) -> NoReturn:
        _fail("bootstrap_job_live_provider.response_invalid")

    def __deepcopy__(self, _memo: Any) -> NoReturn:
        _fail("bootstrap_job_live_provider.response_invalid")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        _fail("bootstrap_job_live_provider.response_invalid")

    def _is_authenticated_for(
        self, plan: BootstrapJobLiveReadPlan, request: _ArmReadRequest
    ) -> bool:
        try:
            return (
                type(self) is AuthenticatedBootstrapJobArmRead
                and self._seal is _RESPONSE_SEAL
                and self._validator(self)
                and plan._is_valid()
                and request._is_valid()
                and any(request is item for item in plan._requests)
                and self.key == request.key
                and self.status == request.expected_status
                and self._resource_id == request.resource_id
                and self._api_version == request.api_version
                and type(self.body_sha256) is str
                and re.fullmatch(r"[0-9a-f]{64}", self.body_sha256) is not None
                and (
                    self.status == 200
                    or hmac.compare_digest(
                        self.body_sha256, hashlib.sha256(b"").hexdigest()
                    )
                )
                and self._request_plan_sha256 == plan.request_plan_sha256
                and type(self.observed_at) is datetime
                and self.observed_at.tzinfo is not None
                and self.observed_at.utcoffset() == timedelta(0)
                and self.observed_at.microsecond == 0
            )
        except Exception:
            return False


def _response_validator(
    *,
    key: str,
    status: int,
    body_sha256: str,
    observed_at: datetime,
    resource_id: str,
    api_version: str,
    request_plan_sha256: str,
) -> Callable[[AuthenticatedBootstrapJobArmRead], bool]:
    """Bind immutable response metadata without retaining provider bytes."""

    def validate(value: AuthenticatedBootstrapJobArmRead) -> bool:
        try:
            return (
                type(value) is AuthenticatedBootstrapJobArmRead
                and value._seal is _RESPONSE_SEAL
                and value.key == key
                and value.status == status
                and hmac.compare_digest(value.body_sha256, body_sha256)
                and value.observed_at == observed_at
                and value._resource_id == resource_id
                and value._api_version == api_version
                and value._request_plan_sha256 == request_plan_sha256
            )
        except Exception:
            return False

    return validate


def _authenticated_response_metadata(
    *,
    plan: BootstrapJobLiveReadPlan,
    request: _ArmReadRequest,
    status: int,
    body_sha256: str,
    observed_at: datetime,
) -> AuthenticatedBootstrapJobArmRead:
    value = object.__new__(AuthenticatedBootstrapJobArmRead)
    metadata = {
        "key": request.key,
        "status": status,
        "body_sha256": body_sha256,
        "observed_at": observed_at,
        "_resource_id": request.resource_id,
        "_api_version": request.api_version,
        "_request_plan_sha256": plan.request_plan_sha256,
        "_seal": _RESPONSE_SEAL,
    }
    validator = _response_validator(
        key=request.key,
        status=status,
        body_sha256=body_sha256,
        observed_at=observed_at,
        resource_id=request.resource_id,
        api_version=request.api_version,
        request_plan_sha256=plan.request_plan_sha256,
    )
    for name, item in {**metadata, "_validator": validator}.items():
        object.__setattr__(value, name, item)
    if not value._is_authenticated_for(plan, request):
        _fail("bootstrap_job_live_provider.response_invalid")
    return value


def _one_shot_claim() -> Callable[[], None]:
    lock = threading.Lock()
    used = False

    def claim() -> None:
        nonlocal used
        with lock:
            if used:
                _fail("bootstrap_job_live_provider.already_used")
            used = True

    return claim


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _monotonic_now() -> float:
    return time.monotonic()


def _read_one(
    request_item: _ArmReadRequest,
    *,
    token: str,
    timeout_seconds: int,
    transport: _TransportConfiguration,
    arm_opener: adapters._HttpOpener,
    deadline_factory: Callable[[int], Any],
) -> tuple[int, str]:
    """Read, validate, digest, and discard one exact ARM response body."""

    query = urllib.parse.urlencode({"api-version": request_item.api_version})
    url = f"{transport.management_origin}{request_item.resource_id}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {token}",
        },
        method="GET",
    )
    try:
        with deadline_factory(timeout_seconds):
            try:
                response = arm_opener.open(request, timeout=timeout_seconds)
            except urllib.error.HTTPError as error:
                try:
                    status = error.code
                    final_url = error.geturl()
                except Exception:
                    _fail("bootstrap_job_live_provider.response_invalid")
                finally:
                    error.close()
                if type(status) is not int or type(final_url) is not str:
                    _fail("bootstrap_job_live_provider.response_invalid")
                if 300 <= status < 400 or final_url != url:
                    _fail("bootstrap_job_live_provider.redirect_refused")
                if status != request_item.expected_status or status != 404:
                    _fail("bootstrap_job_live_provider.provider_refused")
                return status, hashlib.sha256(b"").hexdigest()

            with response:
                try:
                    status = response.status
                    final_url = response.geturl()
                except Exception:
                    _fail("bootstrap_job_live_provider.response_invalid")
                if type(status) is not int or type(final_url) is not str:
                    _fail("bootstrap_job_live_provider.response_invalid")
                if 300 <= status < 400 or final_url != url:
                    _fail("bootstrap_job_live_provider.redirect_refused")
                if status != request_item.expected_status:
                    _fail("bootstrap_job_live_provider.provider_refused")
                if status == 404:
                    return status, hashlib.sha256(b"").hexdigest()
                if status != 200:
                    _fail("bootstrap_job_live_provider.provider_refused")
                observed_status, _media_type, raw = adapters._read_json_response(  # noqa: SLF001
                    response,
                    expected_url=url,
                    maximum_bytes=transport.maximum_response_bytes,
                )
                if observed_status != 200:
                    _fail("bootstrap_job_live_provider.provider_refused")
                adapters._load_mapping(  # noqa: SLF001
                    raw, "bootstrap_job_live_provider.response_invalid"
                )
                return status, hashlib.sha256(raw).hexdigest()
    except SanitizerBootstrapJobLiveProviderFailure:
        raise
    except adapters.RuntimeAdapterFailure as error:
        if str(error) == "http_response.too_large":
            _fail("bootstrap_job_live_provider.response_too_large")
        _fail("bootstrap_job_live_provider.response_invalid")
    except Exception:
        _fail("bootstrap_job_live_provider.provider_unavailable")


class AzureBootstrapJobLiveProvider:
    """One-use, GET-only ARM transport constructed from a sealed plan."""

    __slots__ = ("_execute",)
    _execute: Callable[[], tuple[AuthenticatedBootstrapJobArmRead, ...]]

    def __init__(self) -> None:
        _fail("bootstrap_job_live_provider.transport_invalid")

    def __setattr__(self, _name: str, _value: Any) -> NoReturn:
        _fail("bootstrap_job_live_provider.transport_invalid")

    def __delattr__(self, _name: str) -> NoReturn:
        _fail("bootstrap_job_live_provider.transport_invalid")

    @classmethod
    def _from_reviewed_plan(
        cls,
        *,
        plan: BootstrapJobLiveReadPlan,
        token_source: adapters.ManagedIdentityTokenSource,
    ) -> AzureBootstrapJobLiveProvider:
        if (
            cls is not AzureBootstrapJobLiveProvider
            or type(plan) is not BootstrapJobLiveReadPlan
            or not plan._is_valid()
            or type(token_source) is not adapters.ManagedIdentityTokenSource
            or token_source._client_id != plan._identity_client_id  # noqa: SLF001
        ):
            _fail("bootstrap_job_live_provider.transport_invalid")
        try:
            arm_opener = adapters._default_opener()  # noqa: SLF001
            deadline_factory = adapters._wall_clock_deadline  # noqa: SLF001
            clock = _utc_now
            monotonic = _monotonic_now
        except Exception:
            _fail("bootstrap_job_live_provider.transport_invalid")
        if not callable(getattr(arm_opener, "open", None)) or not all(
            callable(item) for item in (deadline_factory, clock, monotonic)
        ):
            _fail("bootstrap_job_live_provider.transport_invalid")
        claim = _one_shot_claim()

        def execute() -> tuple[AuthenticatedBootstrapJobArmRead, ...]:
            claim()
            if not plan._is_valid():
                _fail("bootstrap_job_live_provider.plan_invalid")
            if (
                type(token_source) is not adapters.ManagedIdentityTokenSource
                or token_source._client_id != plan._identity_client_id  # noqa: SLF001
            ):
                _fail("bootstrap_job_live_provider.transport_invalid")

            # The hash-pinned policy is reopened and compiled into fresh local
            # scalars at the point of consumption. No externally reachable
            # transport configuration can redirect the bearer token.
            transport = _compile_transport_configuration(
                load_bootstrap_job_live_provider_policy()
            )
            started = _monotonic_value(monotonic)
            deadline = started + transport.maximum_elapsed_seconds

            def remaining_timeout() -> int:
                remaining = math.floor(deadline - _monotonic_value(monotonic))
                if remaining < 1:
                    _fail("bootstrap_job_live_provider.deadline")
                return min(transport.request_timeout_seconds, remaining)

            def checkpoint() -> datetime:
                remaining_timeout()
                return _trusted_utc(clock)

            checkpoint()
            if remaining_timeout() < transport.request_timeout_seconds:
                _fail("bootstrap_job_live_provider.deadline")
            try:
                token = token_source.get_token(
                    resource=transport.oauth_resource,
                    timeout_seconds=transport.request_timeout_seconds,
                )
            except Exception:
                _fail("bootstrap_job_live_provider.identity_failed")
            checkpoint()
            responses: list[AuthenticatedBootstrapJobArmRead] = []
            for request_item in plan._requests:
                checkpoint()
                status, body_sha256 = _read_one(
                    request_item,
                    token=token,
                    timeout_seconds=remaining_timeout(),
                    transport=transport,
                    arm_opener=arm_opener,
                    deadline_factory=deadline_factory,
                )
                observed_at = checkpoint()
                responses.append(
                    _authenticated_response_metadata(
                        plan=plan,
                        request=request_item,
                        status=status,
                        body_sha256=body_sha256,
                        observed_at=observed_at,
                    )
                )
            return tuple(responses)

        value = object.__new__(cls)
        object.__setattr__(value, "_execute", execute)
        return value

    def read_all(self) -> tuple[AuthenticatedBootstrapJobArmRead, ...]:
        return self._execute()


def build_bootstrap_job_live_provider() -> NoReturn:
    """Remain terminal until sealed authenticated inputs are implemented."""

    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_bootstrap_job_live_provider_policy(executable="--executable" in sys.argv)
    print("sanitizer bootstrap job live provider valid (dormant)")
