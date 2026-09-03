#!/usr/bin/env python3
"""Dormant, race-safe ARM primitives for one PostgreSQL PITR submission.

The current workflow does not import this module.  A future reviewed binding can
use it only after the canonical policies are activated.  Provider errors and
responses are never rendered by this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from typing import Any, Callable, Mapping, NoReturn, Protocol

ROOT = Path(__file__).resolve().parents[1]
ENVELOPE_POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/restore-envelope-policy.json"
)
SAFE_SUBMIT_POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/restore-safe-submit-policy.json"
)
ENVELOPE_POLICY_SHA256 = (
    "e9d894ef8418c092d61539ddd02e434d8863af4923cdae86468064fd0d65163c"
)
SAFE_SUBMIT_POLICY_SHA256 = (
    "33914160aed0632cc3d563bd1fba8a6de2703588dd06b37a2417470b58deb1b7"
)

REHEARSAL_RE = re.compile(r"^[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_RE = re.compile(r"^[1-9][0-9]*$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
PLACEHOLDER_RE = re.compile(r"^CONFIGURE_EXACT_[A-Z0-9_]+$")
EXPECTED_PLACEHOLDERS = (
    "CONFIGURE_EXACT_CLEANUP_CLIENT_UUID",
    "CONFIGURE_EXACT_CLEANUP_CONTROL_READER_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_CLEANUP_CONTROL_READER_ROLE_PERMISSIONS_SHA256",
    "CONFIGURE_EXACT_CLEANUP_PRINCIPAL_UUID",
    "CONFIGURE_EXACT_CLEANUP_TARGET_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_CLEANUP_TARGET_ROLE_PERMISSIONS_SHA256",
    "CONFIGURE_EXACT_FIREWALL_DENY_POLICY_DEFINITION_ID",
    "CONFIGURE_EXACT_FIREWALL_DENY_POLICY_RULE_SHA256",
    "CONFIGURE_EXACT_PROVISION_CLIENT_UUID",
    "CONFIGURE_EXACT_PROVISION_CONTROL_READER_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_PROVISION_CONTROL_READER_ROLE_PERMISSIONS_SHA256",
    "CONFIGURE_EXACT_PROVISION_PRINCIPAL_UUID",
    "CONFIGURE_EXACT_PROVISION_SOURCE_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_PROVISION_SOURCE_ROLE_PERMISSIONS_SHA256",
    "CONFIGURE_EXACT_PROVISION_TARGET_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_PROVISION_TARGET_ROLE_PERMISSIONS_SHA256",
)
MANAGEMENT_ROOT = "https://management.azure.com"
DEPLOYMENT_TYPE = "microsoft.resources/deployments"
POSTGRES_TYPE = "microsoft.dbforpostgresql/flexibleservers"
UNKNOWN = object()
ISOLATED = "isolated"
ISOLATION_TRANSIENT = "transient"
ISOLATION_REFUSED = "refused"


class SafeSubmitError(Exception):
    """A bounded refusal that contains no provider response text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ArmOutcomeUnknown(Exception):
    """The caller cannot know whether ARM accepted the request."""


def _fail(code: str) -> NoReturn:
    raise SafeSubmitError(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _lower_id(value: object) -> str:
    return str(value or "").rstrip("/").lower()


def _location(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _id(*parts: str) -> str:
    return "/" + "/".join(part.strip("/") for part in parts)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("timestamp_timezone_required")
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _parse_utc(value: object, code: str) -> datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        _fail(code)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        _fail(code)


def _parse_arm_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("policy_read_failed")
    if not isinstance(value, dict):
        _fail("policy_invalid")
    return value


def _placeholder_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            item for nested in value.values() for item in _placeholder_values(nested)
        ]
    if isinstance(value, list):
        return [item for nested in value for item in _placeholder_values(nested)]
    if isinstance(value, str) and PLACEHOLDER_RE.fullmatch(value):
        return [value]
    return []


@dataclass(frozen=True)
class ArmResponse:
    status: int
    body: Any = None
    headers: Mapping[str, str] | None = None


class ArmTransport(Protocol):
    def request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: int,
    ) -> ArmResponse: ...


class AzureArmTransport:
    """Small ARM HTTP adapter with per-request deadlines and bounded errors."""

    def _access_token(self, timeout_seconds: int) -> str:
        command = [
            "az",
            "account",
            "get-access-token",
            "--resource",
            MANAGEMENT_ROOT,
            "--query",
            "accessToken",
            "--output",
            "tsv",
            "--only-show-errors",
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ArmOutcomeUnknown from exc
        token = result.stdout.strip()
        if result.returncode != 0 or not token or any(char.isspace() for char in token):
            _fail("arm_authentication_failed")
        return token

    def request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: int,
    ) -> ArmResponse:
        if not resource_id.startswith("/") or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", api_version
        ):
            _fail("arm_request_target_invalid")
        mutating = method.upper() in {"DELETE", "PATCH", "POST", "PUT"}
        query = urllib.parse.urlencode({"api-version": api_version})
        url = f"{MANAGEMENT_ROOT}{resource_id}?{query}"
        payload = _canonical(body) if body is not None else None
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token(timeout_seconds)}",
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url, data=payload, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    if mutating:
                        raise ArmOutcomeUnknown
                    _fail("arm_response_too_large")
                try:
                    parsed = json.loads(raw) if raw.strip() else None
                except (UnicodeError, json.JSONDecodeError) as exc:
                    if mutating:
                        raise ArmOutcomeUnknown from exc
                    _fail("arm_response_invalid")
                try:
                    status = int(response.status)
                    response_headers = {
                        key.lower(): value for key, value in response.headers.items()
                    }
                except (AttributeError, TypeError, ValueError) as exc:
                    if mutating:
                        raise ArmOutcomeUnknown from exc
                    _fail("arm_response_invalid")
                return ArmResponse(status=status, body=parsed, headers=response_headers)
        except urllib.error.HTTPError as exc:
            return ArmResponse(
                status=exc.code,
                headers={key.lower(): value for key, value in exc.headers.items()},
            )
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise ArmOutcomeUnknown from exc


@dataclass(frozen=True)
class SafeSubmitConfig:
    policy_id: str
    envelope_policy_id: str
    envelope_policy_sha256: str
    subscription_id: str
    target_group_id: str
    source_server_id: str
    provision_client_id: str
    provision_principal_id: str
    location: str
    name_prefix: str
    maximum_ttl_hours: int
    required_tags: dict[str, str]
    deployment_api_version: str
    postgres_api_version: str
    request_timeout_seconds: int
    poll_interval_seconds: int
    intent_poll_attempts: int
    restore_poll_attempts: int
    cleanup_poll_attempts: int
    binding_clock_skew_seconds: int
    late_materialization_watch_hours: int
    intent_name_suffix: str
    submission_name_suffix: str
    cleanup_name_suffix: str
    ready_state: str
    failed_states: frozenset[str]


@dataclass(frozen=True)
class RestoreRequest:
    rehearsal_id: str
    restore_time: datetime
    created_at: datetime
    cleanup_not_after: datetime
    repository: str
    workflow_sha: str
    run_id: str
    run_attempt: str


@dataclass(frozen=True)
class SubmitOutcome:
    status: str
    target_resource_id: str
    intent_sha256: str
    target_observed: bool
    cleanup_required: bool


def _validate_safe_policy(policy: dict[str, Any], *, executable: bool) -> None:
    if set(policy) != {
        "schema_version",
        "policy_id",
        "envelope_policy_id",
        "envelope_policy_sha256",
        "activation",
        "arm",
        "outcomes",
        "retention",
    } or (
        policy.get("schema_version") != 2
        or policy.get("policy_id") != "cosmaneura-staging-restore-safe-submit-v2"
        or policy.get("envelope_policy_id") != "cosmaneura-staging-restore-envelope-v1"
        or policy.get("envelope_policy_sha256") != f"sha256:{ENVELOPE_POLICY_SHA256}"
    ):
        _fail("safe_submit_policy_identity_mismatch")
    activation = policy.get("activation")
    if (
        not isinstance(activation, dict)
        or set(activation)
        != {
            "workflow_integrated",
            "trusted_evidence_binding_implemented",
        }
        or not all(isinstance(value, bool) for value in activation.values())
    ):
        _fail("safe_submit_activation_invalid")
    if executable and activation != {
        "workflow_integrated": True,
        "trusted_evidence_binding_implemented": True,
    }:
        _fail("safe_submit_not_activated")
    arm = policy.get("arm")
    if arm != {
        "management_host": "management.azure.com",
        "deployment_api_version": "2022-09-01",
        "postgres_api_version": "2024-08-01",
        "request_timeout_seconds": 30,
        "poll_interval_seconds": 15,
        "intent_poll_attempts": 20,
        "restore_poll_attempts": 480,
        "cleanup_poll_attempts": 120,
        "binding_clock_skew_seconds": 300,
        "late_materialization_watch_hours": 48,
        "intent_name_suffix": "restore-intent-v2",
        "submission_name_suffix": "restore-submit-v2",
        "cleanup_name_suffix": "restore-cleanup-v2",
    }:
        _fail("safe_submit_arm_policy_mismatch")
    if policy.get("outcomes") != {
        "ready_state": "Ready",
        "failed_states": ["Disabled", "Failed"],
    }:
        _fail("safe_submit_outcome_policy_mismatch")
    if policy.get("retention") != {
        "delete_intent_automatically": False,
        "reason": "Preserve the deterministic cleanup locator until a separately reviewed evidence-retention action proves the restore operation terminal and the exact target absent.",
    }:
        _fail("safe_submit_retention_policy_mismatch")


def validate_canonical_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    if _sha256(ENVELOPE_POLICY_PATH.read_bytes()) != ENVELOPE_POLICY_SHA256:
        _fail("envelope_policy_digest_mismatch")
    if _sha256(SAFE_SUBMIT_POLICY_PATH.read_bytes()) != SAFE_SUBMIT_POLICY_SHA256:
        _fail("safe_submit_policy_digest_mismatch")
    envelope = _read_object(ENVELOPE_POLICY_PATH)
    safe = _read_object(SAFE_SUBMIT_POLICY_PATH)
    if envelope.get("trusted_evidence_binding_implemented") is not False:
        _fail("canonical_envelope_activation_changed")
    if tuple(sorted(_placeholder_values(envelope))) != EXPECTED_PLACEHOLDERS:
        _fail("canonical_envelope_blockers_changed")
    _validate_safe_policy(safe, executable=False)
    if safe["activation"] != {
        "workflow_integrated": False,
        "trusted_evidence_binding_implemented": False,
    }:
        _fail("canonical_safe_submit_activation_changed")
    return envelope, safe


def config_from_policies(
    envelope: dict[str, Any], safe: dict[str, Any], *, executable: bool
) -> SafeSubmitConfig:
    validate_canonical_contracts()
    _validate_safe_policy(safe, executable=executable)
    if (
        envelope.get("policy_id") != safe["envelope_policy_id"]
        or envelope.get("repository") != "CosmaNeura/cosmaneura"
        or envelope.get("protected_ref") != "refs/heads/master"
    ):
        _fail("envelope_policy_identity_mismatch")
    if executable and (
        envelope.get("trusted_evidence_binding_implemented") is not True
        or _placeholder_values(envelope)
    ):
        _fail("envelope_policy_not_activated")
    source = envelope.get("source")
    target = envelope.get("target")
    identities = envelope.get("identities")
    provision = identities.get("provision") if isinstance(identities, dict) else None
    if (
        not isinstance(source, dict)
        or not isinstance(target, dict)
        or not isinstance(provision, dict)
    ):
        _fail("envelope_policy_shape_invalid")
    required_tags = target.get("required_tags")
    if (
        not isinstance(required_tags, dict)
        or not required_tags
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in required_tags.items()
        )
        or not isinstance(target.get("maximum_ttl_hours"), int)
        or not isinstance(provision.get("client_id"), str)
        or not provision["client_id"]
        or not isinstance(provision.get("principal_id"), str)
        or not provision["principal_id"]
    ):
        _fail("envelope_policy_shape_invalid")
    subscription = str(envelope.get("subscription_id", ""))
    group_id = _id(
        "subscriptions", subscription, "resourceGroups", str(target["resource_group"])
    )
    source_id = _id(
        "subscriptions",
        subscription,
        "resourceGroups",
        str(source["resource_group"]),
        "providers/Microsoft.DBforPostgreSQL/flexibleServers",
        str(source["server_name"]),
    )
    arm = safe["arm"]
    return SafeSubmitConfig(
        policy_id=safe["policy_id"],
        envelope_policy_id=envelope["policy_id"],
        envelope_policy_sha256=safe["envelope_policy_sha256"],
        subscription_id=subscription,
        target_group_id=group_id,
        source_server_id=source_id,
        provision_client_id=str(provision.get("client_id", "")),
        provision_principal_id=str(provision.get("principal_id", "")),
        location=str(envelope["location"]),
        name_prefix=str(target["name_prefix"]),
        maximum_ttl_hours=int(target["maximum_ttl_hours"]),
        required_tags=dict(required_tags),
        deployment_api_version=arm["deployment_api_version"],
        postgres_api_version=arm["postgres_api_version"],
        request_timeout_seconds=arm["request_timeout_seconds"],
        poll_interval_seconds=arm["poll_interval_seconds"],
        intent_poll_attempts=arm["intent_poll_attempts"],
        restore_poll_attempts=arm["restore_poll_attempts"],
        cleanup_poll_attempts=arm["cleanup_poll_attempts"],
        binding_clock_skew_seconds=arm["binding_clock_skew_seconds"],
        late_materialization_watch_hours=arm["late_materialization_watch_hours"],
        intent_name_suffix=arm["intent_name_suffix"],
        submission_name_suffix=arm["submission_name_suffix"],
        cleanup_name_suffix=arm["cleanup_name_suffix"],
        ready_state=safe["outcomes"]["ready_state"],
        failed_states=frozenset(safe["outcomes"]["failed_states"]),
    )


class SafeRestoreSubmitter:
    """Persist intent, submit one exact PUT, and reconcile by resource identity."""

    def __init__(
        self,
        transport: ArmTransport,
        config: SafeSubmitConfig,
        *,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.transport = transport
        self.config = config
        self.sleeper = sleeper
        self.clock = clock

    def _trusted_now(self) -> datetime:
        try:
            value = self.clock()
        except Exception as exc:
            raise SafeSubmitError("trusted_clock_unavailable") from exc
        if not isinstance(value, datetime) or value.tzinfo is None:
            _fail("trusted_clock_invalid")
        return value.astimezone(UTC).replace(microsecond=0)

    def _request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ArmResponse | object:
        try:
            return self.transport.request(
                method,
                resource_id,
                api_version=api_version,
                body=body,
                headers=headers,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        except ArmOutcomeUnknown:
            return UNKNOWN

    def _intent_marker_id(self, rehearsal_id: str) -> str:
        return _id(
            self.config.target_group_id,
            "providers/Microsoft.Resources/deployments",
            f"{self.config.name_prefix}-{rehearsal_id}-{self.config.intent_name_suffix}",
        )

    def _submission_marker_id(self, rehearsal_id: str) -> str:
        return _id(
            self.config.target_group_id,
            "providers/Microsoft.Resources/deployments",
            f"{self.config.name_prefix}-{rehearsal_id}-{self.config.submission_name_suffix}",
        )

    def _cleanup_marker_id(self, rehearsal_id: str) -> str:
        return _id(
            self.config.target_group_id,
            "providers/Microsoft.Resources/deployments",
            f"{self.config.name_prefix}-{rehearsal_id}-{self.config.cleanup_name_suffix}",
        )

    def _validate_request(self, request: RestoreRequest) -> None:
        if not REHEARSAL_RE.fullmatch(request.rehearsal_id):
            _fail("rehearsal_id_invalid")
        if (
            request.repository != "CosmaNeura/cosmaneura"
            or not SHA_RE.fullmatch(request.workflow_sha)
            or not RUN_RE.fullmatch(request.run_id)
            or not RUN_RE.fullmatch(request.run_attempt)
        ):
            _fail("workflow_identity_invalid")
        created = _parse_utc(_iso(request.created_at), "created_at_invalid")
        restore = _parse_utc(_iso(request.restore_time), "restore_time_invalid")
        cleanup = _parse_utc(
            _iso(request.cleanup_not_after), "cleanup_not_after_invalid"
        )
        if restore > created or cleanup <= created:
            _fail("restore_timeline_invalid")
        if (cleanup - created).total_seconds() > self.config.maximum_ttl_hours * 3600:
            _fail("cleanup_deadline_out_of_range")

    def _build_intent(self, request: RestoreRequest) -> dict[str, Any]:
        self._validate_request(request)
        cleanup_epoch = int(request.cleanup_not_after.timestamp())
        base = f"{self.config.name_prefix}-{request.rehearsal_id}-e{cleanup_epoch}"
        target_name = f"{base}-pg"
        target_id = _id(
            self.config.target_group_id,
            "providers/Microsoft.DBforPostgreSQL/flexibleServers",
            target_name,
        )
        tags = {
            **self.config.required_tags,
            "expires-epoch": str(cleanup_epoch),
            "rehearsal-id": request.rehearsal_id,
        }
        return {
            "schema_version": 2,
            "policy_id": self.config.policy_id,
            "envelope_policy_id": self.config.envelope_policy_id,
            "envelope_policy_sha256": self.config.envelope_policy_sha256,
            "rehearsal_id": request.rehearsal_id,
            "source_server_resource_id": self.config.source_server_id,
            "provision_client_id": self.config.provision_client_id,
            "provision_principal_id": self.config.provision_principal_id,
            "target_server_resource_id": target_id,
            "target_server_name": target_name,
            "location": self.config.location,
            "restore_time": _iso(request.restore_time),
            "created_at": _iso(request.created_at),
            "cleanup_not_after": _iso(request.cleanup_not_after),
            "repository": request.repository,
            "workflow_sha": request.workflow_sha,
            "run_id": request.run_id,
            "run_attempt": request.run_attempt,
            "required_tags": tags,
        }

    def _deployment_body(self, record: dict[str, Any]) -> dict[str, Any]:
        digest = f"sha256:{_sha256(_canonical(record))}"
        return {
            "properties": {
                "mode": "Incremental",
                "parameters": {
                    "record": {"value": record},
                    "recordSha256": {"value": digest},
                },
                "template": {
                    "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
                    "contentVersion": "1.0.0.0",
                    "parameters": {
                        "record": {"type": "object"},
                        "recordSha256": {"type": "string"},
                    },
                    "resources": [],
                    "outputs": {
                        "record": {"type": "object", "value": "[parameters('record')]"},
                        "recordSha256": {
                            "type": "string",
                            "value": "[parameters('recordSha256')]",
                        },
                    },
                },
            }
        }

    def _record_from_deployment(
        self, response: ArmResponse, marker_id: str
    ) -> tuple[dict[str, Any], str]:
        body = response.body
        if response.status != 200 or not isinstance(body, dict):
            _fail("intent_record_unavailable")
        marker_name = marker_id.rsplit("/", 1)[-1]
        if (
            _lower_id(body.get("id")) != _lower_id(marker_id)
            or str(body.get("name")) != marker_name
            or str(body.get("type", "")).lower() != DEPLOYMENT_TYPE
        ):
            _fail("intent_record_identity_mismatch")
        properties = body.get("properties")
        if not isinstance(properties, dict):
            _fail("intent_record_invalid")
        state = properties.get("provisioningState")
        if not isinstance(state, str):
            _fail("intent_record_invalid")
        if state in {"Canceled", "Failed"}:
            _fail("intent_record_terminal_failure")
        parameters = properties.get("parameters")
        if not isinstance(parameters, dict):
            _fail("intent_record_invalid")
        record_entry = parameters.get("record")
        digest_entry = parameters.get("recordSha256")
        if not isinstance(record_entry, dict) or not isinstance(digest_entry, dict):
            _fail("intent_record_invalid")
        record = record_entry.get("value")
        digest = digest_entry.get("value")
        if (
            not isinstance(record, dict)
            or digest != f"sha256:{_sha256(_canonical(record))}"
        ):
            _fail("intent_record_digest_mismatch")
        return record, state

    def _get_deployment(self, marker_id: str) -> ArmResponse | object | None:
        response = self._request(
            "GET",
            marker_id,
            api_version=self.config.deployment_api_version,
        )
        if response is UNKNOWN:
            return UNKNOWN
        assert isinstance(response, ArmResponse)
        if response.status == 404:
            return None
        if response.status == 200:
            return response
        if response.status == 429 or response.status >= 500:
            return UNKNOWN
        _fail("intent_record_read_refused")

    def _read_deployment_bounded(
        self, marker_id: str
    ) -> dict[str, Any] | object | None:
        indeterminate = False
        for attempt in range(self.config.intent_poll_attempts):
            response = self._get_deployment(marker_id)
            if response is None and not indeterminate:
                return None
            if response is UNKNOWN or response is None:
                indeterminate = True
            else:
                assert isinstance(response, ArmResponse)
                record, state = self._record_from_deployment(response, marker_id)
                if state == "Succeeded":
                    return record
                indeterminate = True
            if attempt + 1 < self.config.intent_poll_attempts:
                self.sleeper(self.config.poll_interval_seconds)
        return UNKNOWN

    def _ensure_deployment(
        self, marker_id: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self._get_deployment(marker_id)
        if isinstance(existing, ArmResponse):
            actual, state = self._record_from_deployment(existing, marker_id)
            if actual != record:
                _fail("intent_record_conflict")
            if state == "Succeeded":
                return actual
        if existing is UNKNOWN:
            _fail("intent_preflight_unknown")
        if existing is None:
            response = self._request(
                "PUT",
                marker_id,
                api_version=self.config.deployment_api_version,
                body=self._deployment_body(record),
                headers={"If-None-Match": "*"},
            )
            if (
                isinstance(response, ArmResponse)
                and response.status
                not in {
                    200,
                    201,
                    202,
                    409,
                    412,
                    429,
                }
                and response.status < 500
            ):
                _fail("intent_record_write_refused")
        for attempt in range(self.config.intent_poll_attempts):
            observed = self._get_deployment(marker_id)
            if isinstance(observed, ArmResponse):
                actual, state = self._record_from_deployment(observed, marker_id)
                if actual != record:
                    _fail("intent_record_conflict")
                if state == "Succeeded":
                    return actual
            if attempt + 1 < self.config.intent_poll_attempts:
                self.sleeper(self.config.poll_interval_seconds)
        _fail("intent_persistence_unproven")

    def _load_intent(self, rehearsal_id: str) -> tuple[dict[str, Any], str]:
        if not REHEARSAL_RE.fullmatch(rehearsal_id):
            _fail("rehearsal_id_invalid")
        marker_id = self._intent_marker_id(rehearsal_id)
        intent = self._read_deployment_bounded(marker_id)
        if intent is UNKNOWN:
            _fail("intent_record_read_unknown")
        if intent is None:
            _fail("intent_record_missing")
        assert isinstance(intent, dict)
        self._validate_intent(intent, rehearsal_id)
        return intent, f"sha256:{_sha256(_canonical(intent))}"

    def _validate_intent(self, intent: dict[str, Any], rehearsal_id: str) -> None:
        required = {
            "schema_version",
            "policy_id",
            "envelope_policy_id",
            "envelope_policy_sha256",
            "rehearsal_id",
            "source_server_resource_id",
            "provision_client_id",
            "provision_principal_id",
            "target_server_resource_id",
            "target_server_name",
            "location",
            "restore_time",
            "created_at",
            "cleanup_not_after",
            "repository",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "required_tags",
        }
        if set(intent) != required or (
            intent.get("schema_version") != 2
            or intent.get("policy_id") != self.config.policy_id
            or intent.get("envelope_policy_id") != self.config.envelope_policy_id
            or intent.get("envelope_policy_sha256")
            != self.config.envelope_policy_sha256
            or intent.get("rehearsal_id") != rehearsal_id
            or _lower_id(intent.get("source_server_resource_id"))
            != _lower_id(self.config.source_server_id)
            or intent.get("provision_client_id") != self.config.provision_client_id
            or intent.get("provision_principal_id")
            != self.config.provision_principal_id
            or _location(intent.get("location")) != _location(self.config.location)
        ):
            _fail("intent_record_invalid")
        created = _parse_utc(intent.get("created_at"), "intent_record_invalid")
        cleanup = _parse_utc(intent.get("cleanup_not_after"), "intent_record_invalid")
        _parse_utc(intent.get("restore_time"), "intent_record_invalid")
        if (
            cleanup <= created
            or (cleanup - created).total_seconds()
            > self.config.maximum_ttl_hours * 3600
        ):
            _fail("intent_record_invalid")

        expected_name = (
            f"{self.config.name_prefix}-{rehearsal_id}-e{int(cleanup.timestamp())}-pg"
        )
        expected_id = _id(
            self.config.target_group_id,
            "providers/Microsoft.DBforPostgreSQL/flexibleServers",
            expected_name,
        )
        expected_tags = {
            **self.config.required_tags,
            "expires-epoch": str(int(cleanup.timestamp())),
            "rehearsal-id": rehearsal_id,
        }
        if (
            intent.get("target_server_name") != expected_name
            or _lower_id(intent.get("target_server_resource_id"))
            != _lower_id(expected_id)
            or intent.get("required_tags") != expected_tags
            or intent.get("repository") != "CosmaNeura/cosmaneura"
            or not SHA_RE.fullmatch(str(intent.get("workflow_sha", "")))
            or not RUN_RE.fullmatch(str(intent.get("run_id", "")))
            or not RUN_RE.fullmatch(str(intent.get("run_attempt", "")))
        ):
            _fail("intent_record_invalid")

    def _intent_expired(self, intent: dict[str, Any]) -> bool:
        created = _parse_utc(intent.get("created_at"), "intent_record_invalid")
        now = self._trusted_now()
        if created > now + timedelta(seconds=self.config.binding_clock_skew_seconds):
            _fail("intent_created_in_future")
        cleanup = _parse_utc(intent.get("cleanup_not_after"), "intent_record_invalid")
        return cleanup <= now

    def _submission_record(
        self, intent: dict[str, Any], intent_digest: str
    ) -> dict[str, Any]:
        body = self._restore_body(intent, intent_digest)
        return {
            "schema_version": 2,
            "kind": "restore-submission-authorized",
            "policy_id": self.config.policy_id,
            "rehearsal_id": intent["rehearsal_id"],
            "intent_sha256": intent_digest,
            "target_server_resource_id": intent["target_server_resource_id"],
            "restore_request_sha256": f"sha256:{_sha256(_canonical(body))}",
            "authorized_at": intent["created_at"],
        }

    def _cleanup_record(
        self, intent: dict[str, Any], intent_digest: str
    ) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "kind": "restore-cleanup-authorized",
            "policy_id": self.config.policy_id,
            "rehearsal_id": intent["rehearsal_id"],
            "intent_sha256": intent_digest,
            "target_server_resource_id": intent["target_server_resource_id"],
            "authorized_at": intent["created_at"],
        }

    def _cleanup_requested(
        self, intent: dict[str, Any], intent_digest: str
    ) -> bool | object:
        marker_id = self._cleanup_marker_id(intent["rehearsal_id"])
        response = self._get_deployment(marker_id)
        if response is UNKNOWN:
            return UNKNOWN
        if response is None:
            return False
        assert isinstance(response, ArmResponse)
        actual, _ = self._record_from_deployment(response, marker_id)
        if actual != self._cleanup_record(intent, intent_digest):
            _fail("cleanup_record_conflict")
        return True

    def _restore_body(
        self, intent: dict[str, Any], intent_digest: str
    ) -> dict[str, Any]:
        tags = {
            **intent["required_tags"],
            "restore-intent-sha256": intent_digest.removeprefix("sha256:"),
        }
        return {
            "location": intent["location"],
            "tags": tags,
            "properties": {
                "createMode": "PointInTimeRestore",
                "sourceServerResourceId": intent["source_server_resource_id"],
                "pointInTimeUTC": intent["restore_time"],
                "network": {"publicNetworkAccess": "Disabled"},
            },
        }

    def _get_target(self, target_id: str) -> ArmResponse | object | None:
        response = self._request(
            "GET", target_id, api_version=self.config.postgres_api_version
        )
        if response is UNKNOWN:
            return UNKNOWN
        assert isinstance(response, ArmResponse)
        if response.status == 404:
            return None
        if response.status == 200:
            return response
        if response.status == 429 or response.status >= 500:
            return UNKNOWN
        _fail("target_read_refused")

    def _verify_target(
        self, response: ArmResponse, intent: dict[str, Any], intent_digest: str
    ) -> dict[str, Any]:
        body = response.body
        if not isinstance(body, dict):
            _fail("target_response_invalid")
        if (
            _lower_id(body.get("id")) != _lower_id(intent["target_server_resource_id"])
            or body.get("name") != intent["target_server_name"]
            or str(body.get("type", "")).lower() != POSTGRES_TYPE
            or _location(body.get("location")) != _location(intent["location"])
        ):
            _fail("target_identity_mismatch")
        tags = body.get("tags")
        if tags is not None and not isinstance(tags, dict):
            _fail("target_binding_mismatch")
        expected_tags = {
            **intent["required_tags"],
            "restore-intent-sha256": intent_digest.removeprefix("sha256:"),
        }
        if isinstance(tags, dict) and any(
            key in tags and tags[key] != value for key, value in expected_tags.items()
        ):
            _fail("target_binding_mismatch")
        properties = body.get("properties")
        if not isinstance(properties, dict):
            _fail("target_response_invalid")
        source = properties.get("sourceServerResourceId")
        restore_time = properties.get("pointInTimeUTC")
        create_mode = properties.get("createMode")
        if source is not None and _lower_id(source) != _lower_id(
            intent["source_server_resource_id"]
        ):
            _fail("target_binding_mismatch")
        if restore_time is not None and restore_time != intent["restore_time"]:
            _fail("target_binding_mismatch")
        if create_mode is not None and create_mode != "PointInTimeRestore":
            _fail("target_binding_mismatch")
        system_data = body.get("systemData")
        system_bound = False
        if system_data is not None:
            if not isinstance(system_data, dict):
                _fail("target_binding_mismatch")
            created_at = _parse_arm_utc(system_data.get("createdAt"))
            lower_bound = _parse_utc(
                intent["created_at"], "intent_record_invalid"
            ) - timedelta(seconds=self.config.binding_clock_skew_seconds)
            upper_bound = _parse_utc(
                intent["cleanup_not_after"], "intent_record_invalid"
            ) + timedelta(hours=self.config.late_materialization_watch_hours)
            if (
                system_data.get("createdByType") != "Application"
                or system_data.get("createdBy")
                not in {
                    intent["provision_client_id"],
                    intent["provision_principal_id"],
                }
                or created_at is None
                or not lower_bound <= created_at <= upper_bound
            ):
                _fail("target_binding_mismatch")
            system_bound = True
        tag_bound = isinstance(tags, dict) and tags.get("restore-intent-sha256") == (
            intent_digest.removeprefix("sha256:")
        )
        restore_bound = (
            _lower_id(source) == _lower_id(intent["source_server_resource_id"])
            and restore_time == intent["restore_time"]
            and create_mode == "PointInTimeRestore"
        )
        if not tag_bound and not restore_bound and not system_bound:
            _fail("target_binding_unproven")
        return body

    def _is_disabled(self, target: dict[str, Any]) -> bool:
        properties = target.get("properties")
        return (
            isinstance(properties, dict)
            and isinstance(properties.get("network"), dict)
            and properties["network"].get("publicNetworkAccess") == "Disabled"
        )

    def _request_isolation(self, target: dict[str, Any], target_id: str) -> str:
        if self._is_disabled(target):
            return ISOLATED
        headers: dict[str, str] = {}
        if isinstance(target.get("etag"), str):
            headers["If-Match"] = target["etag"]
        response = self._request(
            "PATCH",
            target_id,
            api_version=self.config.postgres_api_version,
            body={"properties": {"network": {"publicNetworkAccess": "Disabled"}}},
            headers=headers,
        )
        if response is UNKNOWN:
            return ISOLATION_TRANSIENT
        assert isinstance(response, ArmResponse)
        if response.status in {200, 202, 204}:
            return ISOLATION_TRANSIENT
        if response.status in {404, 409, 412, 429} or response.status >= 500:
            return ISOLATION_TRANSIENT
        return ISOLATION_REFUSED

    def _target_state(self, target: dict[str, Any]) -> str:
        properties = target.get("properties")
        if not isinstance(properties, dict):
            _fail("target_response_invalid")
        state = properties.get("state")
        return state if isinstance(state, str) else "Unknown"

    def _outcome(
        self,
        status: str,
        intent: dict[str, Any],
        intent_digest: str,
        *,
        observed: bool,
        cleanup_required: bool,
    ) -> SubmitOutcome:
        return SubmitOutcome(
            status=status,
            target_resource_id=intent["target_server_resource_id"],
            intent_sha256=intent_digest,
            target_observed=observed,
            cleanup_required=cleanup_required,
        )

    def _cleanup_target(
        self, intent: dict[str, Any], intent_digest: str
    ) -> SubmitOutcome:
        cleanup_marker_id = self._cleanup_marker_id(intent["rehearsal_id"])
        self._ensure_deployment(
            cleanup_marker_id, self._cleanup_record(intent, intent_digest)
        )
        target_id = intent["target_server_resource_id"]
        observed = False
        delete_attempted = False
        for attempt in range(self.config.cleanup_poll_attempts):
            response = self._get_target(target_id)
            if response is UNKNOWN:
                pass
            elif response is None:
                if delete_attempted:
                    return self._outcome(
                        "cleaned",
                        intent,
                        intent_digest,
                        observed=observed,
                        cleanup_required=False,
                    )
            else:
                assert isinstance(response, ArmResponse)
                observed = True
                target = self._verify_target(response, intent, intent_digest)
                self._request_isolation(target, target_id)
                headers: dict[str, str] = {}
                if isinstance(target.get("etag"), str):
                    headers["If-Match"] = target["etag"]
                deleted = self._request(
                    "DELETE",
                    target_id,
                    api_version=self.config.postgres_api_version,
                    headers=headers,
                )
                if deleted is UNKNOWN:
                    delete_attempted = True
                else:
                    assert isinstance(deleted, ArmResponse)
                    if (
                        deleted.status in {200, 202, 204, 404, 409, 412, 429}
                        or deleted.status >= 500
                    ):
                        delete_attempted = True
                    else:
                        _fail("target_cleanup_refused")
            if attempt + 1 < self.config.cleanup_poll_attempts:
                self.sleeper(self.config.poll_interval_seconds)
        status = "cleanup_pending" if observed else "pending_late_materialization"
        return self._outcome(
            status,
            intent,
            intent_digest,
            observed=observed,
            cleanup_required=True,
        )

    def _poll_restore(
        self, intent: dict[str, Any], intent_digest: str
    ) -> SubmitOutcome:
        target_id = intent["target_server_resource_id"]
        for attempt in range(self.config.restore_poll_attempts):
            cleanup_requested = self._cleanup_requested(intent, intent_digest)
            if cleanup_requested is UNKNOWN or cleanup_requested is True:
                return self._cleanup_target(intent, intent_digest)
            if self._intent_expired(intent):
                return self._cleanup_target(intent, intent_digest)
            response = self._get_target(target_id)
            if isinstance(response, ArmResponse):
                target = self._verify_target(response, intent, intent_digest)
                state = self._target_state(target)
                if state in self.config.failed_states:
                    return self._cleanup_target(intent, intent_digest)
                isolation = self._request_isolation(target, target_id)
                if isolation == ISOLATION_REFUSED:
                    return self._cleanup_target(intent, intent_digest)
                cleanup_requested = self._cleanup_requested(intent, intent_digest)
                if cleanup_requested is UNKNOWN or cleanup_requested is True:
                    return self._cleanup_target(intent, intent_digest)
                if self._intent_expired(intent):
                    return self._cleanup_target(intent, intent_digest)
                if state == self.config.ready_state and self._is_disabled(target):
                    return self._outcome(
                        "ready",
                        intent,
                        intent_digest,
                        observed=True,
                        cleanup_required=False,
                    )
            if attempt + 1 < self.config.restore_poll_attempts:
                self.sleeper(self.config.poll_interval_seconds)
        return self._cleanup_target(intent, intent_digest)

    def submit(self, request: RestoreRequest) -> SubmitOutcome:
        expected = self._build_intent(request)
        intent_marker_id = self._intent_marker_id(request.rehearsal_id)
        if self._intent_expired(expected):
            existing_intent = self._read_deployment_bounded(intent_marker_id)
            if existing_intent is UNKNOWN:
                _fail("intent_record_read_unknown")
            if existing_intent is None:
                _fail("intent_expired")
            assert isinstance(existing_intent, dict)
            intent = existing_intent
        else:
            intent = self._ensure_deployment(intent_marker_id, expected)
        self._validate_intent(intent, request.rehearsal_id)
        if intent != expected:
            _fail("intent_record_conflict")
        intent_digest = f"sha256:{_sha256(_canonical(intent))}"
        cleanup_requested = self._cleanup_requested(intent, intent_digest)
        if cleanup_requested is UNKNOWN:
            _fail("cleanup_record_preflight_unknown")
        if cleanup_requested is True:
            return self._cleanup_target(intent, intent_digest)
        submission_marker_id = self._submission_marker_id(request.rehearsal_id)
        existing_submission = self._get_deployment(submission_marker_id)
        submission = self._submission_record(intent, intent_digest)
        if self._intent_expired(intent):
            if existing_submission is UNKNOWN:
                self._ensure_deployment(
                    self._cleanup_marker_id(request.rehearsal_id),
                    self._cleanup_record(intent, intent_digest),
                )
                return self._outcome(
                    "expired_submission_record_unknown",
                    intent,
                    intent_digest,
                    observed=False,
                    cleanup_required=True,
                )
            if existing_submission is None:
                _fail("intent_expired")
            self._ensure_deployment(submission_marker_id, submission)
            return self._cleanup_target(intent, intent_digest)
        target = self._get_target(intent["target_server_resource_id"])
        if target is UNKNOWN:
            _fail("target_preflight_unknown")
        if isinstance(target, ArmResponse) and not isinstance(
            existing_submission, ArmResponse
        ):
            _fail("target_without_submission_record")
        self._ensure_deployment(submission_marker_id, submission)
        if isinstance(target, ArmResponse):
            self._verify_target(target, intent, intent_digest)
            return self._poll_restore(intent, intent_digest)
        if self._intent_expired(intent):
            return self._cleanup_target(intent, intent_digest)
        cleanup_requested = self._cleanup_requested(intent, intent_digest)
        if cleanup_requested is UNKNOWN or cleanup_requested is True:
            return self._cleanup_target(intent, intent_digest)
        response = self._request(
            "PUT",
            intent["target_server_resource_id"],
            api_version=self.config.postgres_api_version,
            body=self._restore_body(intent, intent_digest),
            headers={"If-None-Match": "*"},
        )
        if (
            isinstance(response, ArmResponse)
            and response.status
            not in {
                200,
                201,
                202,
                409,
                412,
                429,
            }
            and response.status < 500
        ):
            _fail("restore_submission_refused")
        cleanup_requested = self._cleanup_requested(intent, intent_digest)
        if cleanup_requested is UNKNOWN or cleanup_requested is True:
            return self._cleanup_target(intent, intent_digest)
        return self._poll_restore(intent, intent_digest)

    def cleanup_intent(self, rehearsal_id: str) -> SubmitOutcome:
        intent, intent_digest = self._load_intent(rehearsal_id)
        submission_id = self._submission_marker_id(rehearsal_id)
        actual = self._read_deployment_bounded(submission_id)
        if actual is UNKNOWN:
            self._ensure_deployment(
                self._cleanup_marker_id(rehearsal_id),
                self._cleanup_record(intent, intent_digest),
            )
            return self._outcome(
                "submission_record_unknown",
                intent,
                intent_digest,
                observed=False,
                cleanup_required=True,
            )
        if actual is None:
            target = self._get_target(intent["target_server_resource_id"])
            if target is None:
                return self._outcome(
                    "prepared_only",
                    intent,
                    intent_digest,
                    observed=False,
                    cleanup_required=False,
                )
            if target is UNKNOWN:
                return self._outcome(
                    "submission_record_unknown",
                    intent,
                    intent_digest,
                    observed=False,
                    cleanup_required=True,
                )
            _fail("submission_record_missing")
        assert isinstance(actual, dict)
        if actual != self._submission_record(intent, intent_digest):
            _fail("submission_record_conflict")
        return self._cleanup_target(intent, intent_digest)
