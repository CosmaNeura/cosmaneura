#!/usr/bin/env python3
"""Dormant ARM-read policy and plan for the sanitizer bootstrap job boundary.

This module has no token acquisition or network execution path. A later reviewed
slice must add an isolated trusted boundary that authenticates the runtime
binding, renewed bootstrap identity, image, provider observation, and semantic
observer before implementing the policy-derived exact GET sequence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, NoReturn, SupportsIndex

try:
    from scripts import (
        staging_restore_sanitizer_bootstrap_job_attachment_contract as attachment,
    )
    from scripts.staging_restore_runtime_adapters import (
        ARM_ORIGIN,
        ARM_RESOURCE,
        MAXIMUM_PROVIDER_BYTES,
        REQUEST_TIMEOUT_SECONDS,
        UUID_RE,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_bootstrap_job_attachment_contract as attachment
    from staging_restore_runtime_adapters import (
        ARM_ORIGIN,
        ARM_RESOURCE,
        MAXIMUM_PROVIDER_BYTES,
        REQUEST_TIMEOUT_SECONDS,
        UUID_RE,
    )


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-bootstrap-job-live-provider-policy.json"
)
POLICY_SHA256 = "54dec3a650054d955b41c41af4c9ee0fe62f53cfb7c720b40b5a0bb4b30e9678"
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
        "bootstrap_job_live_provider.plan_invalid",
        "bootstrap_job_live_provider.policy_invalid",
    }
)


class SanitizerBootstrapJobLiveProviderFailure(RuntimeError):
    """Bounded refusal containing only an allowlisted identifier."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "bootstrap_job_live_provider.policy_invalid"
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
            "management_origin": ARM_ORIGIN,
            "oauth_resource": ARM_RESOURCE,
            "method": "GET",
            "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "maximum_elapsed_seconds": 150,
            "maximum_response_bytes": MAXIMUM_PROVIDER_BYTES,
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
            "dormant_request_plan_compiler_implemented": True,
            "one_shot_get_transport_implemented": False,
            "bootstrap_job_semantic_observer_implemented": False,
            "bootstrap_job_attachment_authenticator_implemented": False,
            "live_provider_calls_enabled": False,
            "workflow_integrated": False,
            "executable": False,
            "activation_ready": False,
            "public_activation_failure": ACTIVATION_FAILURE,
            "activation_blockers": [
                "isolated_transport_boundary_unimplemented",
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
                and UUID_RE.fullmatch(self._identity_client_id) is not None
                and REGISTRY_ID_RE.fullmatch(self._registry_resource_id) is not None
                and UUID_RE.fullmatch(self._role_assignment_name) is not None
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


def _compile_dormant_read_plan(
    *,
    phase: str,
    identity_client_id: str,
    private_registry_resource_id: str,
    private_registry_acrpull_role_assignment_name: str,
) -> BootstrapJobLiveReadPlan:
    """Compile a non-authoritative request plan; never an execution capability."""

    if (
        type(phase) is not str
        or phase not in PHASES
        or type(identity_client_id) is not str
        or UUID_RE.fullmatch(identity_client_id) is None
        or type(private_registry_resource_id) is not str
        or REGISTRY_ID_RE.fullmatch(private_registry_resource_id) is None
        or type(private_registry_acrpull_role_assignment_name) is not str
        or UUID_RE.fullmatch(private_registry_acrpull_role_assignment_name) is None
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


def build_bootstrap_job_live_provider() -> NoReturn:
    """Remain terminal until the isolated authenticated boundary is implemented."""

    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_bootstrap_job_live_provider_policy(executable="--executable" in sys.argv)
    print("sanitizer bootstrap job live provider valid (dormant)")
