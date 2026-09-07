#!/usr/bin/env python3
"""Execute the sealed, catalog-only target-role observation once.

The public factory remains disabled until the target role, identity, offline
authenticator, and stability authorities are bound.  The private constructor
exists for focused verification and for the later reviewed adapter join.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, is_dataclass
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, cast

try:
    from scripts import observe_staging_controller_receipt_v2_database as database
    from scripts import read_staging_runtime_record_material as runtime_ingress
    from scripts import staging_restore_runtime_adapters as runtime_adapters
    from scripts import (
        staging_restore_sanitizer_target_role_observation_frames as frames,
    )
    from scripts import (
        staging_restore_sanitizer_target_role_observation_plan as observation_plan,
    )
    from scripts import (
        staging_restore_sanitizer_target_role_observer_contract as observer_envelope,
    )
    from scripts import staging_restore_sanitizer_target_role_query_grammar as grammar
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import observe_staging_controller_receipt_v2_database as database
    import read_staging_runtime_record_material as runtime_ingress
    import staging_restore_runtime_adapters as runtime_adapters
    import staging_restore_sanitizer_target_role_observation_frames as frames
    import staging_restore_sanitizer_target_role_observation_plan as observation_plan
    import staging_restore_sanitizer_target_role_observer_contract as observer_envelope
    import staging_restore_sanitizer_target_role_query_grammar as grammar


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-target-role-database-observer-contract.json"
)
CONTRACT_SHA256 = "b361317a20d450372eadacd7bee18eec7d1a58686df3ac212283836d347ebe67"
OBSERVER_ENVELOPE_SHA256 = "acf34a80c63b0b103147ccd574195c11ebde16357a24bccf935030bca0f80fad"
OBSERVATION_PLAN_SHA256 = "d341d653195b9b3d3d941304205f801d952e3195cd0eefafd0e9d6a9131a0dbb"
QUERY_GRAMMAR_SHA256 = "8f1c4831c4766942778e5be24149a779ba0cd84081d03c11ed21463aabc3d1fe"
OBSERVATION_FRAMES_SHA256 = "7326d189125f1b01646a0de221eee59ddace31ff353573e8f7186bf77009162b"
DATABASE_OBSERVATION_SHA256 = "66dd80c22dd9f21021ec0c4cf604af5ec6afc5fb5d53bd8e946b72abcf8f43bb"
ACTIVATION_FAILURE = "activation.sanitizer_target_role_offline_authenticator_unimplemented"
OBSERVATION_KIND = "sealed-sanitizer-target-role-database-observation-v1"
MAXIMUM_CONTRACT_BYTES = 64 * 1024
POSTGRES_OAUTH_RESOURCE = "https://ossrdbms-aad.database.windows.net"
TYPE_OIDS = {"boolean": 16, "int64": 20, "int16": 21, "int32": 23, "text": 25}
CLAIMS = {
    "database_connection_authenticated": True,
    "managed_identity_login_authenticated": True,
    "socket_peer_authenticated": True,
    "tls_transport_authenticated": True,
    "read_only_transaction_authenticated": True,
    "exact_query_grammar_executed": True,
    "observation_frames_sealed": True,
    "transaction_executed": True,
    "transaction_committed": False,
    "database_mutation_authority": False,
    "database_role_state_authenticated": False,
    "effective_session_settings_authenticated": False,
    "evidence_publication_authority": False,
    "provider_facts_authenticated": False,
    "runtime_authority": False,
    "sanitizer_authority": False,
    "server_wide_authorization_stability_authenticated": False,
    "workflow_integrated": False,
}
PUBLIC_FIELDS = (
    "kind",
    "database_observer_contract_sha256",
    "observation_plan_sha256",
    "query_grammar_contract_sha256",
    "template_set_sha256",
    "observation_frames_contract_sha256",
    "frame_set_sha256",
    "rehearsal_id",
    "observed_at",
    "expires_at",
    "query_count",
    "row_count",
    "canonical_byte_count",
    *CLAIMS,
)
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        *(
            f"target_role_database_observer.{suffix}"
            for suffix in (
                "already_used contract_invalid configuration_invalid plan_invalid "
                "clock_invalid deadline environment_override identity_failed "
                "connection_failed socket_invalid tls_invalid transaction_invalid "
                "query_invalid query_failed cleanup_failed output_invalid "
                "observation_failed"
            ).split()
        ),
    }
)
_OBSERVATION_SEAL = object()
_ZERO = timedelta(0)


class SanitizerTargetRoleDatabaseObserverFailure(RuntimeError):
    """Bounded refusal that never contains SQL, credentials, or observed rows."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "target_role_database_observer.observation_failed"
    raise SanitizerTargetRoleDatabaseObserverFailure(code) from None


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
        _fail("target_role_database_observer.output_invalid")


def _parse(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("target_role_database_observer.contract_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("target_role_database_observer.contract_invalid")
            result[name] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "target_role_database_observer.contract_invalid"
            ),
        )
    except SanitizerTargetRoleDatabaseObserverFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("target_role_database_observer.contract_invalid")
    if type(value) is not dict:
        _fail("target_role_database_observer.contract_invalid")
    return value


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("target_role_database_observer.contract_invalid")
    return _parse(raw, expected_sha256=CONTRACT_SHA256), raw


def _validate_contract(document: Mapping[str, Any]) -> None:
    try:
        envelope = observer_envelope.load_target_role_observer_contract()
        plan_contract = observation_plan.load_target_role_observation_plan_contract()
        grammar_contract = grammar.load_target_role_query_grammar_contract()
        frames_contract = frames.load_target_role_observation_frames_contract()
        database_policy = database.load_controller_receipt_v2_database_observation_policy()
        connection = document["connection"]
        transaction = document["transaction"]
        execution = document["query_execution"]
        status = document["status"]
    except Exception:
        _fail("target_role_database_observer.contract_invalid")
    expected_upstream = {
        "target_role_observer_envelope": {
            "policy_id": envelope["policy_id"],
            "sha256": f"sha256:{OBSERVER_ENVELOPE_SHA256}",
        },
        "target_role_observation_plan": {
            "policy_id": plan_contract["policy_id"],
            "sha256": f"sha256:{OBSERVATION_PLAN_SHA256}",
        },
        "target_role_query_grammar": {
            "policy_id": grammar_contract["policy_id"],
            "sha256": f"sha256:{QUERY_GRAMMAR_SHA256}",
        },
        "target_role_observation_frames": {
            "policy_id": frames_contract["policy_id"],
            "sha256": f"sha256:{OBSERVATION_FRAMES_SHA256}",
        },
        "controller_database_observation": {
            "policy_id": database_policy["policy_id"],
            "sha256": f"sha256:{DATABASE_OBSERVATION_SHA256}",
        },
    }
    if (
        observer_envelope.CONTRACT_SHA256 != OBSERVER_ENVELOPE_SHA256
        or observation_plan.CONTRACT_SHA256 != OBSERVATION_PLAN_SHA256
        or grammar.CONTRACT_SHA256 != QUERY_GRAMMAR_SHA256
        or frames.CONTRACT_SHA256 != OBSERVATION_FRAMES_SHA256
        or database.POLICY_SHA256 != DATABASE_OBSERVATION_SHA256
        or set(document)
        != {
            "schema_version",
            "policy_id",
            "closed_world",
            "upstream_policies",
            "input",
            "connection",
            "transaction",
            "query_execution",
            "output",
            "claims",
            "status",
        }
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-target-role-database-observer-v1"
        or document.get("closed_world") is not True
        or document.get("upstream_policies") != expected_upstream
        or document.get("input")
        != {
            "only_public_input": "sealed_target_role_observation_plan",
            "required_kind": observation_plan.PLAN_KIND,
            "exact_type_seal_process_local_identity_and_retained_ancestry_required": True,
            "full_retained_ancestry_authentication_required_before_external_effects": True,
            "retained_graph_immutability_and_current_time_recheck_required_before_and_after_every_stage": True,
            "caller_bytes_maps_paths_urls_dsns_hosts_hostaddrs_ports_roles_identity_ids_tokens_credentials_connectors_callbacks_sql_policies_timestamps_or_runtime_overrides_permitted": False,
        }
        or document.get("claims") != CLAIMS
    ):
        _fail("target_role_database_observer.contract_invalid")
    if connection != {
        "implementation_selected": True,
        "source": "sealed_plan_retained_authenticated_ancestry_only",
        "target_fqdn_database_role_database_name_hostaddr_subnet_and_provision_uami_client_id_derived_without_caller_override": True,
        "authentication": "microsoft-entra-access-token-via-explicit-user-assigned-managed-identity",
        "oauth_resource": POSTGRES_OAUTH_RESOURCE,
        "managed_identity_api_version": "2019-08-01",
        "managed_identity_endpoint_source": "system-environment.IDENTITY_ENDPOINT",
        "managed_identity_secret_header_source": "system-environment.IDENTITY_HEADER",
        "managed_identity_response_required_fields": ["access_token", "expires_on"],
        "managed_identity_response_unknown_fields_permitted": True,
        "managed_identity_response_maximum_bytes": 65_536,
        "managed_identity_expiry_skew_seconds": 90,
        "managed_identity_maximum_token_lifetime_seconds": 86_400,
        "maximum_token_requests": 1,
        "token_retention_permitted": False,
        "default_or_ambient_credential_chain_permitted": False,
        "password_or_connection_string_input_permitted": False,
        "maximum_connections": 1,
        "pooling_permitted": False,
        "retry_permitted": False,
        "maximum_identity_request_timeout_seconds": 10,
        "connect_timeout_seconds": 10,
        "maximum_blocking_call_seconds": 5,
        "maximum_elapsed_seconds": 45,
        "application_name": "cosmaneura-staging-sanitizer-target-role-observer-v1",
        "port": 5432,
        "socket_peer_exact_hostaddr_and_port_required": True,
        "socket_local_address_must_be_in_authenticated_executor_subnet": True,
        "ambient_libpq_environment_permitted": False,
        "gss_encryption": "disable",
        "tls_mode": "verify-full",
        "ssl_certificate_authentication": "disable",
        "minimum_tls_version": "TLSv1.2",
        "maximum_tls_version": "TLSv1.3",
        "runtime_ca_bundle_path": "/etc/ssl/certs/ca-certificates.crt",
    }:
        _fail("target_role_database_observer.contract_invalid")
    if transaction != {
        "autocommit": False,
        "driver_isolation_level": "REPEATABLE_READ",
        "isolation": "REPEATABLE READ",
        "read_only": True,
        "commit_permitted": False,
        "always_rollback": True,
        "always_close": True,
        "rollback_timeout_seconds": 1,
        "close_timeout_seconds": 1,
        "fixed_session_options": {
            "default_transaction_read_only": "on",
            "default_transaction_isolation": "repeatable read",
            "statement_timeout": "5s",
            "lock_timeout": "1s",
            "idle_in_transaction_session_timeout": "30s",
            "search_path": "pg_catalog",
            "row_security": "on",
            "synchronous_commit": "on",
        },
        "application_data_reads_permitted": False,
        "role_or_privilege_mutation_permitted": False,
        "ddl_or_dml_permitted": False,
        "advisory_lock_or_external_fence_claimed": False,
    }:
        _fail("target_role_database_observer.contract_invalid")
    if execution != {
        "exact_authenticated_template_bytes_only": True,
        "execute_in_exact_order_once": True,
        "query_count": 13,
        "parameter_count": 0,
        "server_side_prepare_permitted": False,
        "cursor_description_column_names_and_postgresql_type_oids_must_match_before_fetch": True,
        "postgresql_type_oids": TYPE_OIDS,
        "maximum_elapsed_seconds_per_query": 5,
        "maximum_elapsed_seconds_total": 30,
        "row_and_byte_bounds_enforced_while_streaming_and_again_before_sealing": True,
        "duplicate_rows_permitted": False,
        "retry_permitted": False,
    }:
        _fail("target_role_database_observer.contract_invalid")
    if document.get("output") != {
        "kind": OBSERVATION_KIND,
        "public_metadata_only": True,
        "retains_identical_sealed_observation_frames_process_local_only": True,
        "offline_authenticator_must_consume_identical_sealed_observation": True,
        "raw_rows_sql_token_credentials_connection_cursor_connector_or_callback_retention_permitted": False,
        "copy_pickle_json_serialization_or_mutation_permitted": False,
        "repr_or_public_exception_may_contain_target_role_rows_or_credentials": False,
    } or status != {
        "contract_validator_implemented": True,
        "connection_adapter_implemented": True,
        "query_executor_implemented": True,
        "observation_authority_implemented": True,
        "offline_authenticator_implemented": False,
        "live_database_calls_enabled": False,
        "workflow_integrated": False,
        "executable": False,
        "activation_ready": False,
        "public_activation_failure": ACTIVATION_FAILURE,
        "activation_blockers": [
            "exact_database_role_unselected",
            "provision_job_identity_exact_ids_unpinned",
            "target_entra_authentication_and_role_provisioning_unimplemented",
            "offline_authenticator_unimplemented",
            "privileged_global_observer_unimplemented",
            "stability_authorities_unimplemented",
            "post_provision_schema_descriptor_unselected",
            "adapter_join_unimplemented",
        ],
    }:
        _fail("target_role_database_observer.contract_invalid")
    bounds = grammar_contract["resource_bounds"]
    if (
        execution["query_count"] != bounds["maximum_queries"]
        or execution["maximum_elapsed_seconds_per_query"]
        != bounds["maximum_elapsed_seconds_per_query"]
        or execution["maximum_elapsed_seconds_total"]
        != bounds["maximum_elapsed_seconds_total"]
        or tuple(grammar_contract["grammar"]["exact_order"]) != grammar.FRAME_NAMES
    ):
        _fail("target_role_database_observer.contract_invalid")


def validate_target_role_database_observer_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    if type(contract) is not dict or type(executable) is not bool:
        _fail("target_role_database_observer.contract_invalid")
    template, _raw = _read_contract()
    try:
        canonical = _canonical(contract)
        document = _parse(canonical, expected_sha256=hashlib.sha256(canonical).hexdigest())
        if not hmac.compare_digest(_canonical(document), _canonical(template)):
            _fail("target_role_database_observer.contract_invalid")
        _validate_contract(document)
        if executable:
            _fail(ACTIVATION_FAILURE)
        return document
    except SanitizerTargetRoleDatabaseObserverFailure:
        raise
    except Exception:
        _fail("target_role_database_observer.contract_invalid")


def load_target_role_database_observer_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_target_role_database_observer_contract(
        document, executable=executable
    )


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("target_role_database_observer.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != _ZERO
        or value.microsecond
        or value.fold
    ):
        _fail("target_role_database_observer.clock_invalid")
    return value


class _Timeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], float]) -> None:
        if not callable(source):
            _fail("target_role_database_observer.configuration_invalid")
        self._source = source
        self._last: float | None = None

    def read(self) -> float:
        try:
            value = self._source()
        except Exception:
            _fail("target_role_database_observer.clock_invalid")
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            _fail("target_role_database_observer.clock_invalid")
        current = float(value)
        if self._last is not None and current < self._last:
            _fail("target_role_database_observer.clock_invalid")
        self._last = current
        return current


def _remaining(timeline: _Timeline, deadline: float, maximum: int) -> int:
    seconds = math.floor(deadline - timeline.read())
    if seconds < 1:
        _fail("target_role_database_observer.deadline")
    return min(seconds, maximum)


@dataclass(frozen=True, slots=True, repr=False)
class _ConnectionPlan:
    target_fqdn: str
    hostaddr: str
    port: int
    database_name: str
    role_name: str
    provision_client_id: str
    infrastructure_subnet_cidr: str
    rehearsal_id: str
    observation_plan_sha256: str
    expires_at: datetime


def _guard_projection(value: object, seen: set[int]) -> object:
    value_type = type(value)
    if value is None or value_type in {bool, int, str}:
        return value
    if value_type is float:
        if not math.isfinite(cast(float, value)):
            _fail("target_role_database_observer.plan_invalid")
        return ["float", cast(float, value).hex()]
    if value_type is bytes:
        raw = cast(bytes, value)
        return ["bytes", len(raw), hashlib.sha256(raw).hexdigest()]
    if value_type is datetime:
        item = cast(datetime, value)
        return ["datetime", item.isoformat(), item.fold]

    identity = id(value)
    if identity in seen:
        return ["reference", identity]
    seen.add(identity)
    type_name = f"{value_type.__module__}.{value_type.__qualname__}"
    if value_type is object:
        return ["opaque", type_name, identity]
    if value_type in {tuple, list}:
        return [
            type_name,
            identity,
            *(_guard_projection(item, seen) for item in cast(Any, value)),
        ]
    if value_type in {set, frozenset}:
        items = [_guard_projection(item, seen) for item in cast(Any, value)]
        items.sort(key=lambda item: json.dumps(item, sort_keys=True))
        return [type_name, identity, *items]
    if isinstance(value, Mapping):
        items = [
            [_guard_projection(key, seen), _guard_projection(item, seen)]
            for key, item in value.items()
        ]
        items.sort(key=lambda item: json.dumps(item[0], sort_keys=True))
        return [type_name, identity, *items]
    if is_dataclass(value) and not isinstance(value, type):
        names = tuple(field.name for field in dataclass_fields(value))
    else:
        slots = getattr(value_type, "__slots__", ())
        names = (slots,) if type(slots) is str else tuple(slots)
    if not names:
        _fail("target_role_database_observer.plan_invalid")
    return [
        type_name,
        identity,
        *(
            [name, _guard_projection(getattr(value, name), seen)]
            for name in names
        ),
    ]


def _plan_guard(
    value: observation_plan.SanitizerTargetRoleObservationPlan,
) -> bytes:
    try:
        projection = _guard_projection(value, set())
        encoded = json.dumps(
            projection,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(encoded).digest()
    except SanitizerTargetRoleDatabaseObserverFailure:
        raise
    except Exception:
        _fail("target_role_database_observer.plan_invalid")


def _derive_connection_plan(
    value: observation_plan.SanitizerTargetRoleObservationPlan,
    *,
    now: datetime,
) -> _ConnectionPlan:
    try:
        if (
            type(value) is not observation_plan.SanitizerTargetRoleObservationPlan
            or value._seal is not observation_plan._PLAN_SEAL  # noqa: SLF001
            or not value._is_authenticated()  # noqa: SLF001
        ):
            _fail("target_role_database_observer.plan_invalid")
        if now < value.planned_at or not now < value.expires_at:
            _fail("target_role_database_observer.plan_invalid")
        observation = value._database_observation  # noqa: SLF001
        document = database.content._parse_canonical(  # noqa: SLF001
            observation.authority_bytes,
            database.MAXIMUM_POLICY_BYTES,
        )
        production_authority = observation._production_authority  # noqa: SLF001
        production = database.content._parse_canonical(  # noqa: SLF001
            production_authority.authority_bytes,
            database.MAXIMUM_POLICY_BYTES,
        )
        target = document["target"]
        network = document["facts"]["network"]
        identity = production["identity"]
        target_binding = value._target_binding  # noqa: SLF001
        hostaddr = target["executor_private_endpoint_ip"]
        subnet = target["infrastructure_subnet_cidr"]
        host_ip = ipaddress.ip_address(hostaddr)
        network_scope = ipaddress.ip_network(subnet, strict=True)
        if (
            type(host_ip) is not ipaddress.IPv4Address
            or type(network_scope) is not ipaddress.IPv4Network
            or network["peer_address"] != hostaddr
            or target["target_fqdn"] != target_binding.target_fqdn
            or target["database_name"] != target_binding.database_name
            or target["approved_database_role"]
            != target_binding.approved_database_role
            or target["port"] != 5432
            or not database.AZURE_POSTGRES_HOST_RE.fullmatch(target["target_fqdn"])
            or not database.POSTGRES_IDENTIFIER_RE.fullmatch(target["database_name"])
            or not database.POSTGRES_IDENTIFIER_RE.fullmatch(
                target["approved_database_role"]
            )
            or runtime_adapters.UUID_RE.fullmatch(
                identity["provision_job_uami_client_id"]
            )
            is None
        ):
            _fail("target_role_database_observer.plan_invalid")
        return _ConnectionPlan(
            target_fqdn=target["target_fqdn"],
            hostaddr=hostaddr,
            port=5432,
            database_name=target["database_name"],
            role_name=target["approved_database_role"],
            provision_client_id=identity["provision_job_uami_client_id"],
            infrastructure_subnet_cidr=subnet,
            rehearsal_id=value.rehearsal_id,
            observation_plan_sha256=value.observation_plan_sha256,
            expires_at=value.expires_at,
        )
    except SanitizerTargetRoleDatabaseObserverFailure:
        raise
    except Exception:
        _fail("target_role_database_observer.plan_invalid")


def _default_connect(**parameters: Any) -> Any:
    try:
        import psycopg  # pyright: ignore[reportMissingImports]

        return psycopg.connect(**parameters)
    except Exception:
        _fail("target_role_database_observer.connection_failed")


def _connection_parameters(
    plan: _ConnectionPlan, contract: Mapping[str, Any], token: str
) -> dict[str, Any]:
    connection = contract["connection"]
    options = contract["transaction"]["fixed_session_options"]
    encoded_options = " ".join(
        (
            f"-c default_transaction_read_only={options['default_transaction_read_only']}",
            "-c default_transaction_isolation=repeatable\\ read",
            f"-c statement_timeout={options['statement_timeout']}",
            f"-c lock_timeout={options['lock_timeout']}",
            "-c idle_in_transaction_session_timeout="
            + options["idle_in_transaction_session_timeout"],
            f"-c search_path={options['search_path']}",
            f"-c row_security={options['row_security']}",
            f"-c synchronous_commit={options['synchronous_commit']}",
        )
    )
    return {
        "application_name": connection["application_name"],
        "autocommit": False,
        "connect_timeout": connection["connect_timeout_seconds"],
        "dbname": plan.database_name,
        "gssencmode": connection["gss_encryption"],
        "host": plan.target_fqdn,
        "hostaddr": plan.hostaddr,
        "options": encoded_options,
        "password": token,
        "port": plan.port,
        "prepare_threshold": None,
        "ssl_max_protocol_version": connection["maximum_tls_version"],
        "ssl_min_protocol_version": connection["minimum_tls_version"],
        "sslcertmode": connection["ssl_certificate_authentication"],
        "sslmode": connection["tls_mode"],
        "sslrootcert": connection["runtime_ca_bundle_path"],
        "user": plan.role_name,
    }


def _configure_transaction(connection: Any, contract: Mapping[str, Any]) -> None:
    try:
        from psycopg import IsolationLevel  # pyright: ignore[reportMissingImports]

        transaction = contract["transaction"]
        if getattr(connection, "autocommit", None) is not False:
            _fail("target_role_database_observer.transaction_invalid")
        expected = IsolationLevel[transaction["driver_isolation_level"]]
        connection.isolation_level = expected
        connection.read_only = transaction["read_only"]
        if (
            type(connection.isolation_level) is not IsolationLevel
            or connection.isolation_level is not expected
            or connection.read_only is not True
        ):
            _fail("target_role_database_observer.transaction_invalid")
    except SanitizerTargetRoleDatabaseObserverFailure:
        raise
    except Exception:
        _fail("target_role_database_observer.transaction_invalid")


def _connection_info_matches(connection: Any, plan: _ConnectionPlan) -> None:
    try:
        info = connection.info
        if (
            info.dbname != plan.database_name
            or info.user != plan.role_name
            or info.host != plan.target_fqdn
            or info.hostaddr != plan.hostaddr
            or info.port != plan.port
        ):
            _fail("target_role_database_observer.connection_failed")
    except SanitizerTargetRoleDatabaseObserverFailure:
        raise
    except Exception:
        _fail("target_role_database_observer.connection_failed")


def _authenticate_tls(connection: Any) -> None:
    try:
        pgconn = connection.pgconn
        protocol = pgconn.ssl_attribute(b"protocol")
        cipher = pgconn.ssl_attribute(b"cipher")
        if (
            pgconn.ssl_in_use is not True
            or type(protocol) is not bytes
            or protocol not in {b"TLSv1.2", b"TLSv1.3"}
            or type(cipher) is not bytes
            or not 1 <= len(cipher) <= 128
            or any(byte < 0x20 or byte > 0x7E for byte in cipher)
        ):
            _fail("target_role_database_observer.tls_invalid")
    except SanitizerTargetRoleDatabaseObserverFailure:
        raise
    except Exception:
        _fail("target_role_database_observer.tls_invalid")


def _authenticate_socket(
    connection: Any,
    plan: _ConnectionPlan,
    inspector: Callable[[Any], tuple[str, int, str]],
) -> None:
    try:
        peer_address, peer_port, local_address = inspector(connection)
        peer = ipaddress.ip_address(peer_address)
        local = ipaddress.ip_address(local_address)
        subnet = ipaddress.ip_network(plan.infrastructure_subnet_cidr, strict=True)
        if (
            type(peer) is not ipaddress.IPv4Address
            or type(local) is not ipaddress.IPv4Address
            or type(subnet) is not ipaddress.IPv4Network
            or peer_address != plan.hostaddr
            or peer_port != plan.port
            or local not in subnet
            or local in {subnet.network_address, subnet.broadcast_address}
        ):
            _fail("target_role_database_observer.socket_invalid")
    except SanitizerTargetRoleDatabaseObserverFailure:
        raise
    except Exception:
        _fail("target_role_database_observer.socket_invalid")


def _description_matches(description: object, template: Mapping[str, Any]) -> bool:
    columns = template["exact_columns"]
    if type(description) not in {list, tuple}:
        return False
    items = cast(list[Any] | tuple[Any, ...], description)
    if len(items) != len(columns):
        return False
    try:
        return all(
            item.name == expected["name"]
            and item.type_code == TYPE_OIDS[expected["type"]]
            for item, expected in zip(items, columns, strict=True)
        )
    except Exception:
        return False


def _stream_frame(
    connection: Any,
    *,
    name: str,
    statement: str,
    template: Mapping[str, Any],
    maximum_row_bytes: int,
) -> tuple[tuple[object, ...], ...]:
    cursor: Any = None
    failed = False
    try:
        cursor = connection.cursor()
        cursor.execute(statement, prepare=False)
        if not _description_matches(cursor.description, template):
            _fail("target_role_database_observer.query_invalid")
        maximum_rows = template["maximum_rows"]
        raw_rows = cursor.fetchmany(maximum_rows + 1)
        if type(raw_rows) is not list:
            _fail("target_role_database_observer.query_invalid")
        materialized_rows = cast(list[object], raw_rows)
        if len(materialized_rows) > maximum_rows:
            _fail("target_role_database_observer.query_invalid")
        columns = template["exact_columns"]
        output: list[tuple[object, ...]] = []
        digests: set[bytes] = set()
        for raw_row in materialized_rows:
            if type(raw_row) is not tuple or len(raw_row) != len(columns):
                _fail("target_role_database_observer.query_invalid")
            row = tuple(
                frames._decode_scalar(  # noqa: SLF001
                    value,
                    logical_type=column["type"],
                    nullable=column["nullable"],
                )
                for value, column in zip(raw_row, columns, strict=True)
            )
            encoded = _canonical(row)
            digest = hashlib.sha256(encoded).digest()
            if len(encoded) > maximum_row_bytes or digest in digests:
                _fail("target_role_database_observer.query_invalid")
            digests.add(digest)
            output.append(row)
        if name == "session_and_role" and len(output) != 1:
            _fail("target_role_database_observer.query_invalid")
        return tuple(output)
    except SanitizerTargetRoleDatabaseObserverFailure:
        failed = True
        raise
    except frames.SanitizerTargetRoleObservationFramesFailure:
        failed = True
        _fail("target_role_database_observer.query_invalid")
    except Exception:
        failed = True
        _fail("target_role_database_observer.query_failed")
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                if not failed:
                    _fail("target_role_database_observer.query_failed")


def _public_projection(
    *,
    plan: _ConnectionPlan,
    decoded: frames.DecodedSanitizerTargetRoleObservationFrames,
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "kind": OBSERVATION_KIND,
        "database_observer_contract_sha256": f"sha256:{CONTRACT_SHA256}",
        "observation_plan_sha256": plan.observation_plan_sha256,
        "query_grammar_contract_sha256": f"sha256:{QUERY_GRAMMAR_SHA256}",
        "template_set_sha256": f"sha256:{grammar.TEMPLATE_SET_SHA256}",
        "observation_frames_contract_sha256": f"sha256:{OBSERVATION_FRAMES_SHA256}",
        "frame_set_sha256": decoded.frame_set_sha256,
        "rehearsal_id": plan.rehearsal_id,
        "observed_at": observed_at,
        "expires_at": plan.expires_at,
        "query_count": len(grammar.FRAME_NAMES),
        "row_count": decoded.row_count,
        "canonical_byte_count": decoded.canonical_byte_count,
        **CLAIMS,
    }


def _serializable(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    for name in ("observed_at", "expires_at"):
        item = result.get(name)
        if type(item) is not datetime:
            _fail("target_role_database_observer.output_invalid")
        result[name] = item.strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def _proof(
    *,
    public: Mapping[str, Any],
    output_identity: int,
    plan: observation_plan.SanitizerTargetRoleObservationPlan,
    decoded: frames.DecodedSanitizerTargetRoleObservationFrames,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "public": _serializable(public),
                "output_identity": output_identity,
                "seal_identity": id(_OBSERVATION_SEAL),
                "plan_identity": id(plan),
                "plan_seal_identity": id(plan._seal),  # noqa: SLF001
                "frames_identity": id(decoded),
                "frames_seal_identity": id(decoded._seal),  # noqa: SLF001
            }
        )
    ).hexdigest()


class SanitizerTargetRoleDatabaseObservation:
    """Opaque one-shot connection/query observation for offline authentication."""

    __slots__ = (
        *PUBLIC_FIELDS,
        "_plan",
        "_frames",
        "_contract_bytes",
        "_proof_sha256",
        "_seal",
    )

    kind: str
    database_observer_contract_sha256: str
    observation_plan_sha256: str
    query_grammar_contract_sha256: str
    template_set_sha256: str
    observation_frames_contract_sha256: str
    frame_set_sha256: str
    rehearsal_id: str
    observed_at: datetime
    expires_at: datetime
    query_count: int
    row_count: int
    canonical_byte_count: int
    database_connection_authenticated: bool
    managed_identity_login_authenticated: bool
    socket_peer_authenticated: bool
    tls_transport_authenticated: bool
    read_only_transaction_authenticated: bool
    exact_query_grammar_executed: bool
    observation_frames_sealed: bool
    transaction_executed: bool
    transaction_committed: bool
    database_mutation_authority: bool
    database_role_state_authenticated: bool
    effective_session_settings_authenticated: bool
    evidence_publication_authority: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    sanitizer_authority: bool
    server_wide_authorization_stability_authenticated: bool
    workflow_integrated: bool
    _plan: observation_plan.SanitizerTargetRoleObservationPlan
    _frames: frames.DecodedSanitizerTargetRoleObservationFrames
    _contract_bytes: bytes
    _proof_sha256: str
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("target_role_database_observer.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("target_role_database_observer.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("target_role_database_observer.output_invalid")

    def __repr__(self) -> str:
        return "<SanitizerTargetRoleDatabaseObservation sealed>"

    def __copy__(self) -> NoReturn:
        _fail("target_role_database_observer.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("target_role_database_observer.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("target_role_database_observer.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("target_role_database_observer.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            contract = _parse(self._contract_bytes, expected_sha256=CONTRACT_SHA256)
            _validate_contract(contract)
            plan = _derive_connection_plan(self._plan, now=self.observed_at)
            if not self._frames._is_valid():  # noqa: SLF001
                return False
            public = _public_projection(
                plan=plan, decoded=self._frames, observed_at=self.observed_at
            )
            return (
                type(self) is SanitizerTargetRoleDatabaseObservation
                and self._seal is _OBSERVATION_SEAL
                and tuple(public) == PUBLIC_FIELDS
                and all(getattr(self, name) == item for name, item in public.items())
                and hmac.compare_digest(
                    self._proof_sha256,
                    _proof(
                        public=public,
                        output_identity=id(self),
                        plan=self._plan,
                        decoded=self._frames,
                    ),
                )
            )
        except Exception:
            return False


def _mint_observation(
    *,
    plan_value: observation_plan.SanitizerTargetRoleObservationPlan,
    connection_plan: _ConnectionPlan,
    decoded: frames.DecodedSanitizerTargetRoleObservationFrames,
    observed_at: datetime,
    contract_bytes: bytes,
) -> SanitizerTargetRoleDatabaseObservation:
    public = _public_projection(
        plan=connection_plan, decoded=decoded, observed_at=observed_at
    )
    if tuple(public) != PUBLIC_FIELDS:
        _fail("target_role_database_observer.output_invalid")
    value = object.__new__(SanitizerTargetRoleDatabaseObservation)
    for name, item in public.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_plan", plan_value)
    object.__setattr__(value, "_frames", decoded)
    object.__setattr__(value, "_contract_bytes", bytes(contract_bytes))
    object.__setattr__(
        value,
        "_proof_sha256",
        _proof(
            public=public,
            output_identity=id(value),
            plan=plan_value,
            decoded=decoded,
        ),
    )
    object.__setattr__(value, "_seal", _OBSERVATION_SEAL)
    if not value._is_authenticated():
        _fail("target_role_database_observer.output_invalid")
    return value


class ManagedIdentitySanitizerTargetRoleDatabaseObserver:
    """Single-use catalog observer whose public call accepts one sealed plan."""

    __slots__ = (
        "_clock",
        "_connect",
        "_deadline_factory",
        "_epoch_clock",
        "_identity_opener",
        "_lock",
        "_monotonic",
        "_socket_inspector",
        "_used",
    )

    _clock: Callable[[], datetime]
    _identity_opener: Any | None
    _connect: Callable[..., Any]
    _socket_inspector: Callable[[Any], tuple[str, int, str]]
    _epoch_clock: Callable[[], float]
    _monotonic: Callable[[], float]
    _deadline_factory: Callable[[int], Any]
    _lock: Any
    _used: bool

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("target_role_database_observer.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        identity_opener: Any | None = None,
        connect: Callable[..., Any] = _default_connect,
        socket_inspector: Callable[[Any], tuple[str, int, str]] = database._socket_addresses,  # noqa: SLF001
        epoch_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        deadline_factory: Callable[[int], Any] = runtime_adapters._wall_clock_deadline,  # noqa: SLF001
    ) -> ManagedIdentitySanitizerTargetRoleDatabaseObserver:
        if (
            cls is not ManagedIdentitySanitizerTargetRoleDatabaseObserver
            or not callable(clock)
            or (
                identity_opener is not None
                and not callable(getattr(identity_opener, "open", None))
            )
            or not callable(connect)
            or not callable(socket_inspector)
            or not callable(epoch_clock)
            or not callable(monotonic)
            or not callable(deadline_factory)
        ):
            _fail("target_role_database_observer.configuration_invalid")
        value = object.__new__(cls)
        for name, item in {
            "_clock": clock,
            "_identity_opener": identity_opener,
            "_connect": connect,
            "_socket_inspector": socket_inspector,
            "_epoch_clock": epoch_clock,
            "_monotonic": monotonic,
            "_deadline_factory": deadline_factory,
            "_lock": threading.Lock(),
            "_used": False,
        }.items():
            object.__setattr__(value, name, item)
        return value

    def observe(
        self, *, plan: observation_plan.SanitizerTargetRoleObservationPlan
    ) -> SanitizerTargetRoleDatabaseObservation:
        code = "target_role_database_observer.observation_failed"
        try:
            return self._observe(plan)
        except Exception as error:
            if (
                type(error) is SanitizerTargetRoleDatabaseObserverFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
            ):
                code = error.args[0]
        raise SanitizerTargetRoleDatabaseObserverFailure(code) from None

    def _observe(
        self, plan_value: observation_plan.SanitizerTargetRoleObservationPlan
    ) -> SanitizerTargetRoleDatabaseObservation:
        with self._lock:
            if self._used:
                _fail("target_role_database_observer.already_used")
            object.__setattr__(self, "_used", True)
        contract, contract_bytes = _read_contract()
        _validate_contract(contract)
        query_contract = grammar.load_target_role_query_grammar_contract()
        templates, _template_bytes = grammar._read_templates()  # noqa: SLF001
        timeline = _Timeline(self._monotonic)
        started_at = _trusted_utc(self._clock)
        connection_policy = contract["connection"]
        overall_deadline = (
            timeline.read() + connection_policy["maximum_elapsed_seconds"]
        )
        connection_plan = _derive_connection_plan(plan_value, now=started_at)
        plan_guard = _plan_guard(plan_value)

        def checkpoint(limit: float = overall_deadline) -> datetime:
            current = _trusted_utc(self._clock)
            if (
                not hmac.compare_digest(_plan_guard(plan_value), plan_guard)
                or current < plan_value.planned_at
                or not current < connection_plan.expires_at
            ):
                _fail("target_role_database_observer.plan_invalid")
            if timeline.read() > limit:
                _fail("target_role_database_observer.deadline")
            return current

        try:
            database._reject_libpq_environment()  # noqa: SLF001
        except Exception:
            _fail("target_role_database_observer.environment_override")
        identity_opener = self._identity_opener or runtime_ingress._default_identity_opener()  # noqa: SLF001
        token = ""
        connection: Any = None
        cleanup_failed = False
        raw_frames: list[tuple[str, tuple[tuple[object, ...], ...]]] = []
        try:
            try:
                token = runtime_ingress._managed_identity_token(  # noqa: SLF001
                    client_id=connection_plan.provision_client_id,
                    resource=POSTGRES_OAUTH_RESOURCE,
                    contract=connection_policy,
                    identity_opener=identity_opener,
                    identity_endpoint=os.environ.get("IDENTITY_ENDPOINT", ""),
                    identity_header=os.environ.get("IDENTITY_HEADER", ""),
                    epoch_timeline=runtime_ingress._Timeline(  # noqa: SLF001
                        self._epoch_clock,
                        "target_role_database_observer.identity_clock_invalid",
                    ),
                    timeout_seconds=_remaining(
                        timeline,
                        overall_deadline,
                        connection_policy["maximum_identity_request_timeout_seconds"],
                    ),
                    deadline_factory=self._deadline_factory,
                )
            except SanitizerTargetRoleDatabaseObserverFailure:
                raise
            except Exception:
                _fail("target_role_database_observer.identity_failed")
            checkpoint()
            parameters = _connection_parameters(connection_plan, contract, token)
            try:
                with self._deadline_factory(
                    _remaining(
                        timeline,
                        overall_deadline,
                        connection_policy["connect_timeout_seconds"],
                    )
                ):
                    connection = self._connect(**parameters)
            except SanitizerTargetRoleDatabaseObserverFailure:
                raise
            except Exception:
                _fail("target_role_database_observer.connection_failed")
            finally:
                parameters["password"] = ""
            checkpoint()
            with self._deadline_factory(
                _remaining(
                    timeline,
                    overall_deadline,
                    connection_policy["maximum_blocking_call_seconds"],
                )
            ):
                _connection_info_matches(connection, connection_plan)
                _configure_transaction(connection, contract)
                _authenticate_socket(
                    connection, connection_plan, self._socket_inspector
                )
                _authenticate_tls(connection)
            checkpoint()
            query_deadline = min(
                overall_deadline,
                timeline.read()
                + contract["query_execution"]["maximum_elapsed_seconds_total"],
            )
            template_specs = query_contract["grammar"]["templates"]
            for name, template in zip(grammar.FRAME_NAMES, template_specs, strict=True):
                checkpoint(query_deadline)
                with self._deadline_factory(
                    _remaining(
                        timeline,
                        query_deadline,
                        contract["query_execution"][
                            "maximum_elapsed_seconds_per_query"
                        ],
                    )
                ):
                    rows = _stream_frame(
                        connection,
                        name=name,
                        statement=templates[name],
                        template=template,
                        maximum_row_bytes=query_contract["resource_bounds"][
                            "maximum_row_bytes"
                        ],
                    )
                raw_frames.append((name, rows))
                if name == "session_and_role" and (
                    rows[0][0] != connection_plan.database_name
                    or rows[0][1] != connection_plan.role_name
                    or rows[0][2] != connection_plan.role_name
                    or rows[0][4] != connection_plan.role_name
                    or rows[0][13] != "origin"
                ):
                    _fail("target_role_database_observer.query_invalid")
                checkpoint(query_deadline)
            decoded = frames._decode_target_role_observation_frames(  # noqa: SLF001
                tuple(raw_frames)
            )
            checkpoint(query_deadline)
        except SanitizerTargetRoleDatabaseObserverFailure:
            raise
        except Exception:
            _fail("target_role_database_observer.observation_failed")
        finally:
            token = ""
            if connection is not None:
                for name, maximum in (
                    (
                        "rollback",
                        contract["transaction"]["rollback_timeout_seconds"],
                    ),
                    ("close", contract["transaction"]["close_timeout_seconds"]),
                ):
                    try:
                        seconds = _remaining(timeline, overall_deadline, maximum)
                    except Exception:
                        seconds = maximum
                        cleanup_failed = True
                    try:
                        with self._deadline_factory(seconds):
                            getattr(connection, name)()
                    except Exception:
                        cleanup_failed = True
            if cleanup_failed:
                _fail("target_role_database_observer.cleanup_failed")
        observed_at = checkpoint(overall_deadline)
        result = _mint_observation(
            plan_value=plan_value,
            connection_plan=connection_plan,
            decoded=decoded,
            observed_at=observed_at,
            contract_bytes=contract_bytes,
        )
        checkpoint(overall_deadline)
        return result


def build_sanitizer_target_role_database_observer() -> NoReturn:
    """Refuse public construction until all downstream authorities are bound."""

    load_target_role_database_observer_contract()
    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_target_role_database_observer_contract(executable="--executable" in sys.argv)
    print("sanitizer target-role database observer contract valid (dormant)")
