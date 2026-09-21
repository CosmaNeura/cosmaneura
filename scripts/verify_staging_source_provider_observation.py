#!/usr/bin/env python3
"""Verify one Azure PostgreSQL source observation without restore capability.

The checked-in policy is deliberately disabled. This module defines only a
narrow GET-only provider protocol and has no network, subprocess, credential,
marker, restore, sanitizer, deployment, or database capability.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, Protocol

try:
    from scripts import authenticate_staging_server_scope_receipt as server_auth
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import authenticate_staging_server_scope_receipt as server_auth


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/source-provider-observation-policy.json"
)
POLICY_SHA256 = "5a5774b26852421e01388317d89fb64d093fdf5d1e49ad04165c88bdf6f3fb52"
MAXIMUM_POLICY_BYTES = 1024 * 1024
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MANIFEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{11,63}$")
MIME_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
MIME_QUOTED_RE = re.compile(r'^"(?:[\x20-\x21\x23-\x5b\x5d-\x7e]|\\[\x20-\x7e])*"$')
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
PROVIDER_UTC_RE = re.compile(
    r"^(20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.([0-9]{1,7}))?Z$"
)
RECEIPT_FIELDS = (
    "schema_version",
    "policy_id",
    "policy_sha256",
    "base_binding_sha256",
    "manifest_id",
    "evidence_set_sha256",
    "source_sha",
    "candidate_sha",
    "provider_kind",
    "management_origin",
    "provider_api_version",
    "source_server_resource_id",
    "source_server_name",
    "source_server_type",
    "source_location",
    "source_database_name",
    "source_fqdn",
    "source_postgresql_major",
    "source_state",
    "source_public_network_access",
    "source_backup_retention_days",
    "source_geo_redundant_backup",
    "source_replication_role",
    "approved_restore_time",
    "earliest_restore_time",
    "restore_mode",
    "restore_window_claim",
    "provider_response_sha256",
    "observed_at",
    "expires_at",
    "pass",
)
ACTIVATION_BLOCKERS = (
    "source_provider_observation_policy_disabled",
    "trusted_provider_transport_unimplemented",
    "attestation_verification_unimplemented",
    "composite_binding_unimplemented",
    "workflow_integration_unimplemented",
    "restore_activation_unimplemented",
)
OUTPUT_ACTIVATION_BLOCKERS = (
    "attestation_verification_unimplemented",
    "composite_binding_unimplemented",
    "workflow_integration_unimplemented",
    "restore_activation_unimplemented",
)


class ProviderObservationFailure(RuntimeError):
    """Bounded provider-observation failure safe to report without payloads."""


def _fail(code: str) -> NoReturn:
    raise ProviderObservationFailure(code)


@dataclass(frozen=True, slots=True)
class ProviderObservationResponse:
    status: int
    content_type: str
    body: bytes = dataclass_field(repr=False)


class SourceObservationProvider(Protocol):
    def get_flexible_server(
        self,
        *,
        management_origin: str,
        resource_id: str,
        api_version: str,
        timeout_seconds: int,
    ) -> ProviderObservationResponse: ...


_VERIFIED_PROVIDER_OBSERVATION_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class VerifiedProviderObservation:
    """Opaque provider receipt minted after one exact response is validated.

    The identity guard rejects deserialized or accidentally substituted values;
    it is not a sandbox against arbitrary code executing in this process.
    """

    _receipt_bytes: bytes = dataclass_field(repr=False)
    _receipt_sha256: str = dataclass_field(repr=False)
    _observation_seal: object = dataclass_field(repr=False, compare=False)

    def _is_observer_minted(self) -> bool:
        raw = getattr(self, "_receipt_bytes", None)
        digest = getattr(self, "_receipt_sha256", None)
        return (
            type(self) is VerifiedProviderObservation
            and getattr(self, "_observation_seal", None)
            is _VERIFIED_PROVIDER_OBSERVATION_SEAL
            and type(raw) is bytes
            and isinstance(digest, str)
            and DIGEST_RE.fullmatch(digest) is not None
            and _digest(raw) == digest
        )

    @property
    def receipt_bytes(self) -> bytes:
        if not self._is_observer_minted():
            _fail("provider_observation.invalid")
        return self._receipt_bytes

    @property
    def receipt_sha256(self) -> str:
        if not self._is_observer_minted():
            _fail("provider_observation.invalid")
        return self._receipt_sha256

    @property
    def receipt(self) -> dict[str, Any]:
        value = _load_json(self.receipt_bytes, "provider_observation.invalid")
        if set(value) != set(RECEIPT_FIELDS) or _canonical(value) != self.receipt_bytes:
            _fail("provider_observation.invalid")
        return dict(value)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ProviderObservationFailure("provider_observation.non_canonical") from None


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _load_json(raw: bytes, code: str) -> Mapping[str, Any]:
    if type(raw) is not bytes:
        _fail(code)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def reject_nonfinite(_value: str) -> NoReturn:
        _fail(code)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except ProviderObservationFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProviderObservationFailure(code) from None
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _expect_mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _parse_utc(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise ProviderObservationFailure(code) from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return parsed


def _parse_provider_utc(value: Any) -> tuple[datetime, int, str]:
    if not isinstance(value, str):
        _fail("provider_restore_window.invalid")
    match = PROVIDER_UTC_RE.fullmatch(value)
    if match is None:
        _fail("provider_restore_window.invalid")
    try:
        parsed = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=UTC
        )
    except ValueError:
        raise ProviderObservationFailure("provider_restore_window.invalid") from None
    fractional_ticks = int((match.group(2) or "0").ljust(7, "0"))
    return parsed, fractional_ticks, value


def _trusted_now(clock: Callable[[], datetime]) -> tuple[datetime, str]:
    try:
        value = clock()
    except Exception:
        raise ProviderObservationFailure("provider_clock.unavailable") from None
    try:
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            or value.microsecond != 0
        ):
            _fail("provider_clock.invalid")
        normalized = value.astimezone(UTC)
        rendered = normalized.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ProviderObservationFailure:
        raise
    except Exception:
        raise ProviderObservationFailure("provider_clock.invalid") from None
    return normalized, rendered


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _lower_id(value: Any) -> str:
    if not isinstance(value, str):
        _fail("provider_response.binding_mismatch")
    return value.rstrip("/").lower()


def _location(value: Any) -> str:
    if not isinstance(value, str):
        _fail("provider_response.binding_mismatch")
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_json_media_type(value: Any, expected: str) -> bool:
    if type(value) is not str or any(not 32 <= ord(char) <= 126 for char in value):
        return False

    parts: list[str] = []
    start = 0
    quoted = False
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == ";" and not quoted:
            parts.append(value[start:index])
            start = index + 1
    if quoted or escaped:
        return False
    parts.append(value[start:])

    if parts[0].strip().lower() != expected:
        return False
    seen: set[str] = set()
    for parameter in parts[1:]:
        parameter = parameter.strip()
        if not parameter or "=" not in parameter:
            return False
        name, parameter_value = parameter.split("=", 1)
        if (
            not name.strip()
            or not parameter_value.strip()
            or MIME_TOKEN_RE.fullmatch(name.strip()) is None
        ):
            return False
        normalized_name = name.strip().lower()
        if normalized_name in seen:
            return False
        seen.add(normalized_name)
        parameter_value = parameter_value.strip()
        if (
            MIME_TOKEN_RE.fullmatch(parameter_value) is None
            and MIME_QUOTED_RE.fullmatch(parameter_value) is None
        ):
            return False
    return True


def _read_policy_bytes(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError:
        raise ProviderObservationFailure("provider_policy.read_failed") from None
    if type(raw) is not bytes or not 0 < len(raw) <= MAXIMUM_POLICY_BYTES:
        _fail("provider_policy.invalid")
    return raw


def _expect_exact_keys(
    value: Any, expected: Collection[str], code: str
) -> Mapping[str, Any]:
    mapping = _expect_mapping(value, code)
    if set(mapping) != set(expected):
        _fail(code)
    return mapping


def validate_provider_observation_policy(
    policy: Any, *, executable: bool
) -> Mapping[str, Any]:
    policy = _expect_exact_keys(
        policy,
        {
            "schema_version",
            "policy_id",
            "activation_ready",
            "activation",
            "server_scope_authentication_policy",
            "source",
            "provider",
            "restore_capability",
            "receipt_fields",
            "activation_blockers",
            "output_activation_blockers",
        },
        "provider_policy.invalid",
    )
    activation = _expect_exact_keys(
        policy["activation"],
        {
            "trusted_provider_transport_implemented",
            "workflow_integrated",
            "network_access_enabled",
        },
        "provider_policy.invalid",
    )
    authentication_policy = _expect_exact_keys(
        policy["server_scope_authentication_policy"],
        {"policy_id", "sha256"},
        "provider_policy.invalid",
    )
    provider_policy = _expect_exact_keys(
        policy["provider"],
        {
            "kind",
            "management_origin",
            "oauth_resource",
            "http_method",
            "api_version",
            "request_timeout_seconds",
            "maximum_response_bytes",
            "response_media_type",
            "redirects_permitted",
            "maximum_requests",
        },
        "provider_policy.invalid",
    )
    source = _expect_exact_keys(
        policy["source"],
        {
            "subscription_id",
            "resource_group",
            "resource_id",
            "resource_type",
            "server_name",
            "database_name",
            "fqdn",
            "location",
            "postgresql_major",
            "state",
            "public_network_access",
            "backup_retention_days",
            "geo_redundant_backup",
            "replication_role",
        },
        "provider_policy.invalid",
    )
    restore_capability = _expect_exact_keys(
        policy["restore_capability"],
        {
            "mode",
            "earliest_restore_date_path",
            "latest_restore_bound",
            "maximum_receipt_lifetime_seconds",
            "claim",
        },
        "provider_policy.invalid",
    )
    if (
        type(policy["schema_version"]) is not int
        or policy["schema_version"] != 1
        or policy["policy_id"] != "cosmaneura-staging-source-provider-observation-v1"
        or type(policy["activation_ready"]) is not bool
        or any(type(value) is not bool for value in activation.values())
        or authentication_policy
        != {
            "policy_id": "cosmaneura-staging-server-scope-authentication-v1",
            "sha256": f"sha256:{server_auth.POLICY_SHA256}",
        }
        or provider_policy
        != {
            "kind": "azure-resource-manager",
            "management_origin": "https://management.azure.com",
            "oauth_resource": "https://management.azure.com/",
            "http_method": "GET",
            "api_version": "2024-08-01",
            "request_timeout_seconds": 30,
            "maximum_response_bytes": 1048576,
            "response_media_type": "application/json",
            "redirects_permitted": False,
            "maximum_requests": 1,
        }
        or source
        != {
            "subscription_id": "bab2a709-1fcb-4c87-a9fc-802f7477ec13",
            "resource_group": "cosmaneura-staging",
            "resource_id": (
                "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/"
                "resourceGroups/cosmaneura-staging/providers/"
                "Microsoft.DBforPostgreSQL/flexibleServers/"
                "cosmaneura-staging-postgres"
            ),
            "resource_type": "Microsoft.DBforPostgreSQL/flexibleServers",
            "server_name": "cosmaneura-staging-postgres",
            "database_name": "staging",
            "fqdn": "cosmaneura-staging-postgres.postgres.database.azure.com",
            "location": "northcentralus",
            "postgresql_major": "16",
            "state": "Ready",
            "public_network_access": "Enabled",
            "backup_retention_days": 7,
            "geo_redundant_backup": "Disabled",
            "replication_role": "Primary",
        }
        or restore_capability
        != {
            "mode": "PointInTimeRestore",
            "earliest_restore_date_path": "properties.backup.earliestRestoreDate",
            "latest_restore_bound": "trusted_observation_time",
            "maximum_receipt_lifetime_seconds": 60,
            "claim": "provider_advertised_window_only",
        }
        or policy["receipt_fields"] != list(RECEIPT_FIELDS)
        or policy["activation_blockers"] != list(ACTIVATION_BLOCKERS)
        or policy["output_activation_blockers"] != list(OUTPUT_ACTIVATION_BLOCKERS)
        or type(provider_policy["request_timeout_seconds"]) is not int
        or type(provider_policy["maximum_response_bytes"]) is not int
        or type(provider_policy["redirects_permitted"]) is not bool
        or type(provider_policy["maximum_requests"]) is not int
        or type(source["backup_retention_days"]) is not int
        or type(restore_capability["maximum_receipt_lifetime_seconds"]) is not int
    ):
        _fail("provider_policy.invalid")
    if executable and (
        policy["activation_ready"] is not True
        or any(value is not True for value in activation.values())
    ):
        _fail("provider_policy.disabled")
    return policy


def _parse_policy_document(
    raw: bytes, claimed_sha256: str, *, executable: bool
) -> Mapping[str, Any]:
    if (
        type(raw) is not bytes
        or not isinstance(claimed_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", claimed_sha256) is None
        or hashlib.sha256(raw).hexdigest() != claimed_sha256
    ):
        _fail("provider_policy.hash_mismatch")
    policy = _load_json(raw, "provider_policy.invalid")
    return validate_provider_observation_policy(policy, executable=executable)


def load_provider_observation_policy_document(
    path: Path = POLICY_PATH,
) -> tuple[Mapping[str, Any], str, bytes]:
    raw = _read_policy_bytes(path)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != POLICY_SHA256:
        _fail("provider_policy.hash_mismatch")
    policy = _parse_policy_document(raw, digest, executable=False)
    return policy, digest, raw


def _validate_upstream_binding(
    binding: server_auth.ServerAuthenticatedSafeSubmitBinding,
    policy: Mapping[str, Any],
) -> tuple[Mapping[str, Any], bytes, datetime, datetime]:
    if (
        type(binding) is not server_auth.ServerAuthenticatedSafeSubmitBinding
        or not binding._is_authenticator_minted()
    ):
        _fail("server_authenticated_binding.invalid")
    raw = binding.record_bytes
    if not 0 < len(raw) <= MAXIMUM_POLICY_BYTES:
        _fail("server_authenticated_binding.invalid")
    record = _expect_exact_keys(
        _load_json(raw, "server_authenticated_binding.invalid"),
        server_auth.SERVER_AUTHENTICATED_SAFE_SUBMIT_FIELDS,
        "server_authenticated_binding.invalid",
    )
    digest_fields = {
        "evidence_manifest_policy_sha256",
        "manifest_file_sha256",
        "bundle_index_sha256",
        "evidence_set_sha256",
        "candidate_receipt_sha256",
        "inventory_receipt_sha256",
        "quiescence_receipt_sha256",
        "server_scope_receipt_sha256",
        "server_scope_authentication_receipt_sha256",
    }
    if (
        _canonical(record) != raw
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or record["binding_policy_id"]
        != "cosmaneura-staging-restore-evidence-binding-v1"
        or record["evidence_manifest_policy_id"]
        != "cosmaneura-staging-evidence-manifest-v2"
        or record["evidence_manifest_policy_sha256"]
        != f"sha256:{server_auth.evidence_manifest.POLICY_SHA256}"
        or any(
            type(record[field]) is not str or DIGEST_RE.fullmatch(record[field]) is None
            for field in digest_fields
        )
        or type(record["manifest_id"]) is not str
        or MANIFEST_ID_RE.fullmatch(record["manifest_id"]) is None
        or type(record["source_sha"]) is not str
        or SHA_RE.fullmatch(record["source_sha"]) is None
        or type(record["candidate_sha"]) is not str
        or SHA_RE.fullmatch(record["candidate_sha"]) is None
        or _lower_id(record["source_server_resource_id"])
        != _lower_id(policy["source"]["resource_id"])
        or record["source_database_name"] != policy["source"]["database_name"]
    ):
        _fail("server_authenticated_binding.invalid")
    approved = _parse_utc(
        record["approved_restore_time"], "server_authenticated_binding.invalid"
    )
    observed = _parse_utc(record["observed_at"], "server_authenticated_binding.invalid")
    expires = _parse_utc(record["expires_at"], "server_authenticated_binding.invalid")
    if (
        approved != observed
        or expires <= observed
        or (expires - observed).total_seconds() > 900
    ):
        _fail("server_authenticated_binding.invalid")
    return record, raw, approved, expires


def _observe_source(
    provider: SourceObservationProvider, policy: Mapping[str, Any]
) -> ProviderObservationResponse:
    try:
        response = provider.get_flexible_server(
            management_origin=policy["provider"]["management_origin"],
            resource_id=policy["source"]["resource_id"],
            api_version=policy["provider"]["api_version"],
            timeout_seconds=policy["provider"]["request_timeout_seconds"],
        )
    except Exception:
        raise ProviderObservationFailure("provider.unavailable") from None
    if type(response) is not ProviderObservationResponse:
        _fail("provider_response.invalid")
    return response


def _verify_provider_observation_with_document(
    *,
    policy_bytes: bytes,
    policy_sha256: str,
    binding: server_auth.ServerAuthenticatedSafeSubmitBinding,
    provider: SourceObservationProvider,
    clock: Callable[[], datetime] = _utc_now,
) -> tuple[dict[str, Any], VerifiedProviderObservation]:
    policy = _parse_policy_document(policy_bytes, policy_sha256, executable=True)
    base, base_bytes, approved_restore_time, base_expires = _validate_upstream_binding(
        binding, policy
    )
    response = _observe_source(provider, policy)
    provider_policy = policy["provider"]
    if (
        type(response.status) is not int
        or response.status != 200
        or not _is_json_media_type(
            response.content_type, provider_policy["response_media_type"]
        )
        or type(response.body) is not bytes
        or not 0 < len(response.body) <= provider_policy["maximum_response_bytes"]
    ):
        _fail("provider_response.invalid")
    document = _load_json(response.body, "provider_response.invalid")
    properties = _expect_mapping(
        document.get("properties"), "provider_response.invalid"
    )
    network = _expect_mapping(properties.get("network"), "provider_response.invalid")
    backup = _expect_mapping(properties.get("backup"), "provider_response.invalid")
    source = policy["source"]
    retention = backup.get("backupRetentionDays")
    if (
        _lower_id(document.get("id")) != _lower_id(source["resource_id"])
        or document.get("name") != source["server_name"]
        or not isinstance(document.get("type"), str)
        or document["type"].lower() != source["resource_type"].lower()
        or _location(document.get("location")) != source["location"]
        or properties.get("fullyQualifiedDomainName") != source["fqdn"]
        or properties.get("version") != source["postgresql_major"]
        or properties.get("state") != source["state"]
        or network.get("publicNetworkAccess") != source["public_network_access"]
        or type(retention) is not int
        or retention != source["backup_retention_days"]
        or backup.get("geoRedundantBackup") != source["geo_redundant_backup"]
        or properties.get("replicationRole") != source["replication_role"]
    ):
        _fail("provider_response.binding_mismatch")
    earliest, earliest_fraction, earliest_text = _parse_provider_utc(
        backup.get("earliestRestoreDate")
    )
    observed, observed_text = _trusted_now(clock)
    if (
        observed < approved_restore_time
        or observed >= base_expires
        or earliest > approved_restore_time
        or (earliest == approved_restore_time and earliest_fraction > 0)
    ):
        _fail("provider_observation.stale")
    restore_capability = policy["restore_capability"]
    expires = min(
        base_expires,
        observed
        + timedelta(seconds=restore_capability["maximum_receipt_lifetime_seconds"]),
    )
    receipt = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": f"sha256:{policy_sha256}",
        "base_binding_sha256": _digest(base_bytes),
        "manifest_id": base["manifest_id"],
        "evidence_set_sha256": base["evidence_set_sha256"],
        "source_sha": base["source_sha"],
        "candidate_sha": base["candidate_sha"],
        "provider_kind": provider_policy["kind"],
        "management_origin": provider_policy["management_origin"],
        "provider_api_version": provider_policy["api_version"],
        "source_server_resource_id": source["resource_id"],
        "source_server_name": source["server_name"],
        "source_server_type": source["resource_type"],
        "source_location": source["location"],
        "source_database_name": source["database_name"],
        "source_fqdn": source["fqdn"],
        "source_postgresql_major": source["postgresql_major"],
        "source_state": source["state"],
        "source_public_network_access": source["public_network_access"],
        "source_backup_retention_days": source["backup_retention_days"],
        "source_geo_redundant_backup": source["geo_redundant_backup"],
        "source_replication_role": source["replication_role"],
        "approved_restore_time": base["approved_restore_time"],
        "earliest_restore_time": earliest_text,
        "restore_mode": restore_capability["mode"],
        "restore_window_claim": restore_capability["claim"],
        "provider_response_sha256": _digest(response.body),
        "observed_at": observed_text,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pass": True,
    }
    if set(receipt) != set(RECEIPT_FIELDS):
        _fail("provider_receipt.invalid")
    receipt_bytes = _canonical(receipt)
    receipt_sha256 = _digest(receipt_bytes)
    verified = object.__new__(VerifiedProviderObservation)
    object.__setattr__(verified, "_receipt_bytes", receipt_bytes)
    object.__setattr__(verified, "_receipt_sha256", receipt_sha256)
    object.__setattr__(
        verified,
        "_observation_seal",
        _VERIFIED_PROVIDER_OBSERVATION_SEAL,
    )
    result = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "provider_observation_receipt": receipt,
        "provider_receipt_sha256": receipt_sha256,
        "activation_state": "blocked",
        "activation_blockers": policy["output_activation_blockers"],
    }
    return result, verified


def verify_provider_observation() -> NoReturn:
    """Refuse until the exact checked-in policy and trusted transport are ready."""

    policy, _digest_value, _raw = load_provider_observation_policy_document()
    validate_provider_observation_policy(policy, executable=True)
    _fail("trusted_provider_transport.unimplemented")
