#!/usr/bin/env python3
"""Dormant authenticated PostgreSQL observation for controller-receipt-v2."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import stat
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import observe_staging_controller_receipt_v2_arm as arm
    from scripts import read_staging_controller_receipt_v2 as ingress
    from scripts import read_staging_runtime_record_material as runtime_ingress
    from scripts import staging_restore_runtime_adapters as adapters
    from scripts import validate_staging_controller_receipt_v2 as content
    from scripts import validate_staging_controller_receipt_v2_production as production
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import observe_staging_controller_receipt_v2_arm as arm
    import read_staging_controller_receipt_v2 as ingress
    import read_staging_runtime_record_material as runtime_ingress
    import staging_restore_runtime_adapters as adapters
    import validate_staging_controller_receipt_v2 as content
    import validate_staging_controller_receipt_v2_production as production
    import validate_staging_restore_runtime_binding as runtime_binding


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/controller-receipt-v2-database-observation-policy.json"
)
POLICY_SHA256 = "66dd80c22dd9f21021ec0c4cf604af5ec6afc5fb5d53bd8e946b72abcf8f43bb"
MAXIMUM_POLICY_BYTES = 64 * 1024
POSTGRES_OAUTH_RESOURCE = "https://ossrdbms-aad.database.windows.net"
DEFAULT_WALL_CLOCK_DEADLINE = adapters._wall_clock_deadline
AZURE_POSTGRES_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.postgres\.database\.azure\.com$"
)
POSTGRES_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
CATALOG_ALLOWLIST = tuple(
    "pg_catalog.pg_auth_members pg_catalog.pg_authid pg_catalog.pg_namespace "
    "pg_catalog.pg_roles pg_catalog.pg_stat_ssl".split()
)
FUNCTION_ALLOWLIST = tuple(
    "pg_catalog.current_database pg_catalog.current_setting "
    "pg_catalog.has_database_privilege pg_catalog.has_schema_privilege "
    "pg_catalog.has_table_privilege pg_catalog.jsonb_agg "
    "pg_catalog.jsonb_build_array pg_catalog.pg_backend_pid "
    "pg_catalog.pg_is_in_recovery pg_catalog.to_regrole".split()
)
FORBIDDEN_ROLE_NAMES = tuple(
    "azure_pg_admin pg_checkpoint pg_create_subscription pg_execute_server_program pg_maintain pg_monitor pg_read_all_data pg_read_all_settings pg_read_all_stats pg_read_server_files pg_signal_backend pg_stat_scan_tables pg_use_reserved_connections pg_write_all_data pg_write_server_files".split()
)
DRIVER_ISOLATION_LEVEL = "REPEATABLE_READ"
SESSION_QUERY = (
    "SELECT pg_catalog.current_database()::text,session_user::text,"
    "current_user::text,pg_catalog.current_setting('server_version_num')::integer,"
    "pg_catalog.current_setting('application_name')::text,"
    "pg_catalog.pg_is_in_recovery()::boolean,"
    "pg_catalog.current_setting('transaction_read_only')::text,"
    "pg_catalog.current_setting('transaction_isolation')::text,"
    "pg_catalog.current_setting('search_path')::text,"
    "pg_catalog.current_setting('statement_timeout')::text,"
    "pg_catalog.current_setting('lock_timeout')::text,"
    "pg_catalog.current_setting('idle_in_transaction_session_timeout')::text,"
    "pg_catalog.current_setting('default_transaction_read_only')::text,"
    "pg_catalog.current_setting('default_transaction_isolation')::text"
)
TLS_QUERY = (
    "SELECT ssl::boolean,version::text,cipher::text,bits::integer "
    "FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid()"
)
ROLE_QUERY = (
    "SELECT r.rolname::text,r.rolcanlogin::boolean,r.rolsuper::boolean,"
    "r.rolinherit::boolean,r.rolcreaterole::boolean,r.rolcreatedb::boolean,"
    "r.rolreplication::boolean,r.rolbypassrls::boolean,r.rolconnlimit::integer,"
    "(r.rolvaliduntil IS NULL OR r.rolvaliduntil>CURRENT_TIMESTAMP)::boolean,"
    "pg_catalog.has_database_privilege(current_user,pg_catalog.current_database(),"
    "'CONNECT')::boolean,pg_catalog.has_database_privilege(current_user,"
    "pg_catalog.current_database(),'CREATE')::boolean,"
    "pg_catalog.has_database_privilege(current_user,pg_catalog.current_database(),"
    "'TEMPORARY')::boolean,pg_catalog.has_schema_privilege(current_user,n.oid,"
    "'USAGE')::boolean,pg_catalog.has_schema_privilege(current_user,n.oid,"
    "'CREATE')::boolean,pg_catalog.has_table_privilege(current_user,"
    "'pg_catalog.pg_authid','SELECT')::boolean,(SELECT COALESCE("
    "pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(g.rolname,m.admin_option,"
    "m.inherit_option,m.set_option) ORDER BY g.rolname),'[]'::jsonb)::text FROM "
    "pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles g ON g.oid=m.roleid "
    "WHERE m.member=r.oid) FROM pg_catalog.pg_roles r CROSS JOIN "
    "pg_catalog.pg_namespace n WHERE r.oid=pg_catalog.to_regrole(current_user) "
    "AND n.nspname='public'"
)
ADDITIONAL_LIBPQ_ENVIRONMENT = frozenset(
    {
        "OPENSSL_CONF",
        "OPENSSL_ENGINES",
        "OPENSSL_MODULES",
        "PGSERVICEFILE",
        "PGSYSCONFDIR",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
    }
)

_PLAN_SEAL = object()
_AUTHORITY_SEAL = object()
_FAILURE_CODES = frozenset(
    f"controller_receipt_database.{suffix}"
    for suffix in "already_used authority_invalid authority_unsealed cleanup_failed clock_invalid configuration_invalid connection_failed deadline environment_override facts_invalid identity_failed observation_failed plan_invalid plan_unsealed policy_disabled policy_invalid query_failed query_invalid socket_invalid transaction_failed transaction_invalid".split()
)


class ControllerReceiptV2DatabaseObservationFailure(RuntimeError):
    """Bounded refusal safe to report without credentials or evidence."""


def _fail(code: str) -> NoReturn:
    raise ControllerReceiptV2DatabaseObservationFailure(code) from None


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
            _fail("controller_receipt_database.policy_invalid")
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
            _fail("controller_receipt_database.policy_invalid")
    except ControllerReceiptV2DatabaseObservationFailure:
        raise
    except OSError:
        _fail("controller_receipt_database.policy_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("controller_receipt_database.policy_invalid")
    if type(value) is not dict:
        _fail("controller_receipt_database.policy_invalid")
    return value


def validate_controller_receipt_v2_database_observation_policy(
    policy: Any, *, executable: bool = False
) -> dict[str, Any]:
    template = _read_policy()
    try:
        document = json.loads(content._canonical(policy))
    except Exception:
        _fail("controller_receipt_database.policy_invalid")
    activation = document.get("activation", {})
    claims = document.get("claims", {})
    observation = document.get("observation", {})
    connection = observation.get("connection", {})
    queries = observation.get("queries", {})
    session = observation.get("session", {})
    transport = observation.get("transport", {})
    transaction = observation.get("transaction", {})
    upstream = document.get("upstream_policies", {})
    expected_claims = {
        "arm_network_facts_authenticated": True,
        "blob_or_redis_copy_absence_proven": False,
        "controller_receipt_exists": False,
        "create_only_behavior_proven": False,
        "database_mutation_authority": False,
        "database_role_authenticated": True,
        "executable": False,
        "password_authentication_absence_proven": False,
        "private_route_reachability_authenticated": True,
        "provider_facts_authenticated": False,
        "runtime_authority": False,
        "sanitizer_authority": False,
        "tls_transport_authenticated": True,
        "writer_identity_use_authenticated": False,
    }
    if (
        type(executable) is not bool
        or content._canonical(document) != content._canonical(template)
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-controller-receipt-v2-database-observation-v1"
        or document.get("closed_world") is not True
        or document.get("activation_ready") is not False
        or activation
        != {
            "consumer_binding_implemented": False,
            "controller_receipt_builder_integrated": False,
            "database_observer_implemented": True,
            "executable": False,
            "live_database_calls_enabled": False,
            "workflow_integrated": False,
        }
        or not document.get("activation_blockers")
        or claims != expected_claims
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
        or upstream.get("arm_observation")
        != {
            "policy_id": "cosmaneura-staging-controller-receipt-v2-arm-observation-v1",
            "sha256": f"sha256:{arm.POLICY_SHA256}",
        }
        or upstream.get("restore_runtime_binding")
        != {
            "policy_id": "cosmaneura-staging-restore-runtime-binding-v1",
            "sha256": f"sha256:{runtime_binding.CANONICAL_POLICY_SHA256}",
        }
        or document.get("input", {}).get("only_public_inputs")
        != ["production_authority", "arm_observation"]
        or document["input"].get(
            "caller_clock_database_dsn_host_hostaddr_path_policy_role_token_or_transport_selector_permitted"
        )
        is not False
        or connection.get("maximum_connections") != 1
        or connection.get("maximum_elapsed_seconds") != 30
        or connection.get("maximum_blocking_call_seconds") != 10
        or connection.get("maximum_observation_age_seconds") != 60
        or connection.get("no_pool") is not True
        or connection.get("autocommit") is not False
        or connection.get("port") != 5432
        or connection.get("prepare_threshold") is not None
        or connection.get("retry_permitted") is not False
        or connection.get("socket_peer_exact_match_required") is not True
        or session
        != {
            "default_transaction_isolation": "repeatable read",
            "default_transaction_read_only": "on",
            "idle_in_transaction_session_timeout": "10s",
            "in_recovery": False,
            "lock_timeout": "1s",
            "postgresql_major_version": 16,
            "search_path": "pg_catalog",
            "statement_timeout": "5s",
        }
        or queries.get("exact_order") != ["session", "tls", "role"]
        or queries.get("maximum_queries") != 3
        or queries.get("maximum_rows_per_query") != 1
        or queries.get("statements")
        != {"role": ROLE_QUERY, "session": SESSION_QUERY, "tls": TLS_QUERY}
        or queries.get("role")
        != {
            "allowed_memberships": [],
            "forbidden_role_names": list(FORBIDDEN_ROLE_NAMES),
            "membership_record_fields": [
                "role_name",
                "admin_option",
                "inherit_option",
                "set_option",
            ],
            "required_connection_limit": 1,
            "required_flags": {
                "rolbypassrls": False,
                "rolcanlogin": True,
                "rolcreatedb": False,
                "rolcreaterole": False,
                "rolinherit": True,
                "rolreplication": False,
                "rolsuper": False,
            },
            "required_privileges": {
                "database_connect": True,
                "database_create": False,
                "database_temporary": False,
                "pg_authid_select": False,
                "public_schema_create": False,
                "public_schema_usage": True,
            },
        }
        or transaction
        != {
            "always_close": True,
            "always_rollback": True,
            "application_data_queries_permitted": False,
            "catalogs": list(CATALOG_ALLOWLIST),
            "close_timeout_seconds": 1,
            "commit_permitted": False,
            "connection_autocommit": False,
            "driver_isolation_level": DRIVER_ISOLATION_LEVEL,
            "driver_read_only": True,
            "functions": list(FUNCTION_ALLOWLIST),
            "isolation": "REPEATABLE READ",
            "read_only": True,
            "rollback_timeout_seconds": 1,
        }
        or transport.get("oauth_resource") != POSTGRES_OAUTH_RESOURCE
        or transport.get("managed_identity_api_version") != "2019-08-01"
        or transport.get("managed_identity_expiry_skew_seconds") != 90
        or transport.get("managed_identity_maximum_token_lifetime_seconds") != 86_400
        or transport.get("managed_identity_response_maximum_bytes") != 65_536
        or transport.get("managed_identity_response_unknown_fields_permitted")
        is not True
        or transport.get("maximum_request_timeout_seconds") != 10
        or transport.get("maximum_token_requests") != 1
        or transport.get("minimum_tls_version") != "TLSv1.2"
        or transport.get("sslmode") != "verify-full"
        or transport.get("sslcertmode") != "disable"
        or transport.get("ssl_min_protocol_version") != "TLSv1.2"
        or transport.get("ssl_max_protocol_version") != "TLSv1.3"
        or transport.get("sslrootcert") != "/etc/ssl/certs/ca-certificates.crt"
        or transport.get("ambient_libpq_environment_permitted") is not False
    ):
        _fail("controller_receipt_database.policy_invalid")
    if executable:
        _fail("controller_receipt_database.policy_disabled")
    return document


def load_controller_receipt_v2_database_observation_policy(
    *, executable: bool = False
) -> dict[str, Any]:
    return validate_controller_receipt_v2_database_observation_policy(
        _read_policy(), executable=executable
    )


class _Timeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], float]) -> None:
        if not callable(source):
            _fail("controller_receipt_database.configuration_invalid")
        self._source = source
        self._last: float | None = None

    def read(self) -> float:
        try:
            value = self._source()
        except Exception:
            _fail("controller_receipt_database.clock_invalid")
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            _fail("controller_receipt_database.clock_invalid")
        current = float(value)
        if self._last is not None and current < self._last:
            _fail("controller_receipt_database.clock_invalid")
        self._last = current
        return current


def _remaining(timeline: _Timeline, deadline: float, maximum: int) -> int:
    value = math.floor(deadline - timeline.read())
    if value < 1:
        _fail("controller_receipt_database.deadline")
    return min(value, maximum)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _DatabasePlan:
    target_fqdn: str
    hostaddr: str
    port: int
    database_name: str
    role_name: str
    provision_client_id: str
    infrastructure_subnet_id: str
    infrastructure_subnet_cidr: str
    expires_at: datetime
    rehearsal_id: str
    execution_resource_id: str
    production_authority_sha256: str
    arm_observation_sha256: str
    sha256: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_database.plan_unsealed")

    def _document(self) -> dict[str, Any]:
        return {
            "arm_observation_sha256": self.arm_observation_sha256,
            "database_name": self.database_name,
            "execution_resource_id": self.execution_resource_id,
            "expires_at": self.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hostaddr": self.hostaddr,
            "infrastructure_subnet_cidr": self.infrastructure_subnet_cidr,
            "infrastructure_subnet_id": self.infrastructure_subnet_id,
            "port": self.port,
            "production_authority_sha256": self.production_authority_sha256,
            "provision_client_id": self.provision_client_id,
            "rehearsal_id": self.rehearsal_id,
            "role_name": self.role_name,
            "target_fqdn": self.target_fqdn,
        }

    def _is_valid(self) -> bool:
        return (
            type(self) is _DatabasePlan
            and self._seal is _PLAN_SEAL
            and self.sha256 == content._digest(content._canonical(self._document()))
        )


def _derive_plan(
    production_authority: production.ControllerReceiptV2ProductionAuthority,
    arm_observation: arm.AuthenticatedControllerReceiptV2ArmNetworkObservation,
    *,
    policy: Mapping[str, Any],
    now: datetime,
) -> _DatabasePlan:
    try:
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
            or now.microsecond != 0
            or type(production_authority)
            is not production.ControllerReceiptV2ProductionAuthority
            or type(arm_observation)
            is not arm.AuthenticatedControllerReceiptV2ArmNetworkObservation
            or arm_observation._production_authority is not production_authority
            or not production_authority._is_authenticated()
            or not arm_observation._is_authenticated()
        ):
            _fail("controller_receipt_database.authority_invalid")
        runtime, controller, _production, production_document = arm._fresh_context(
            production_authority, now
        )
        arm_document = arm_observation.authority
        target = arm_document["target"]
        connection = policy["observation"]["connection"]
        target_fqdn = target["target_fqdn"]
        hostaddr = target["executor_private_ip_address"]
        endpoint_cidr = ipaddress.ip_network(
            target["executor_private_endpoint_subnet_cidr"], strict=True
        )
        infrastructure = target["configured_executor_infrastructure_subnet"]
        infrastructure_cidr = ipaddress.ip_network(infrastructure["cidr"], strict=True)
        host_address = ipaddress.ip_address(hostaddr)
        database_name = production_document["source"]["source_database_name"]
        role_name = controller["fixed_bindings"]["approved_database_role"]
        expires_at = min(
            content._utc(arm_document["expires_at"]),
            content._utc(production_document["launch"]["launch_expires_at"]),
            content._utc(production_document["request"]["cleanup_not_after"]),
        )
        expected_fqdn = (
            production_document["request"]["target_server_name"]
            + controller["fixed_bindings"]["target_postgres_fqdn_suffix"]
        )
        runtime_environment = runtime["runtime"]["container_apps_environment"]
        if (
            type(target_fqdn) is not str
            or AZURE_POSTGRES_HOST_RE.fullmatch(target_fqdn) is None
            or target_fqdn != expected_fqdn
            or type(hostaddr) is not str
            or type(host_address) is not ipaddress.IPv4Address
            or not host_address.is_private
            or host_address not in endpoint_cidr
            or host_address in infrastructure_cidr
            or type(endpoint_cidr) is not ipaddress.IPv4Network
            or type(infrastructure_cidr) is not ipaddress.IPv4Network
            or endpoint_cidr.overlaps(infrastructure_cidr)
            or infrastructure["id"]
            != runtime_environment["infrastructure_subnet_resource_id"]
            or infrastructure["cidr"]
            != runtime_environment["infrastructure_subnet_cidr"]
            or type(database_name) is not str
            or POSTGRES_IDENTIFIER_RE.fullmatch(database_name) is None
            or type(role_name) is not str
            or POSTGRES_IDENTIFIER_RE.fullmatch(role_name) is None
            or role_name in FORBIDDEN_ROLE_NAMES
            or arm_observation.observed_at > now
            or now - arm_observation.observed_at
            > timedelta(seconds=connection["maximum_observation_age_seconds"])
            or not now < expires_at
            or arm_document["rehearsal_id"] != production_authority.rehearsal_id
            or arm_document["execution_resource_id"]
            != production_authority.execution_binding
            or arm_document["production_authority_sha256"]
            != content._digest(production_authority.authority_bytes)
            or arm_document["claims"]["arm_network_facts_authenticated"] is not True
        ):
            _fail("controller_receipt_database.plan_invalid")
        fields = {
            "target_fqdn": target_fqdn,
            "hostaddr": hostaddr,
            "port": connection["port"],
            "database_name": database_name,
            "role_name": role_name,
            "provision_client_id": production_document["identity"][
                "provision_job_uami_client_id"
            ],
            "infrastructure_subnet_id": infrastructure["id"],
            "infrastructure_subnet_cidr": infrastructure["cidr"],
            "expires_at": expires_at,
            "rehearsal_id": production_authority.rehearsal_id,
            "execution_resource_id": production_authority.execution_binding,
            "production_authority_sha256": content._digest(
                production_authority.authority_bytes
            ),
            "arm_observation_sha256": content._digest(arm_observation.authority_bytes),
        }
        value = object.__new__(_DatabasePlan)
        for name, item in fields.items():
            object.__setattr__(value, name, item)
        object.__setattr__(
            value,
            "sha256",
            content._digest(
                content._canonical(
                    {
                        "arm_observation_sha256": fields["arm_observation_sha256"],
                        "database_name": fields["database_name"],
                        "execution_resource_id": fields["execution_resource_id"],
                        "expires_at": fields["expires_at"].strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        "hostaddr": fields["hostaddr"],
                        "infrastructure_subnet_cidr": fields[
                            "infrastructure_subnet_cidr"
                        ],
                        "infrastructure_subnet_id": fields["infrastructure_subnet_id"],
                        "port": fields["port"],
                        "production_authority_sha256": fields[
                            "production_authority_sha256"
                        ],
                        "provision_client_id": fields["provision_client_id"],
                        "rehearsal_id": fields["rehearsal_id"],
                        "role_name": fields["role_name"],
                        "target_fqdn": fields["target_fqdn"],
                    }
                )
            ),
        )
        object.__setattr__(value, "_seal", _PLAN_SEAL)
        if not value._is_valid():
            _fail("controller_receipt_database.plan_invalid")
        return value
    except ControllerReceiptV2DatabaseObservationFailure:
        raise
    except Exception:
        _fail("controller_receipt_database.plan_invalid")


def _reject_libpq_environment() -> None:
    try:
        from psycopg import pq

        names = {
            option.envvar.decode("ascii")
            for option in pq.Conninfo.get_defaults()
            if option.envvar
        }
    except Exception:
        _fail("controller_receipt_database.configuration_invalid")
    if any(name in os.environ for name in names | ADDITIONAL_LIBPQ_ENVIRONMENT):
        _fail("controller_receipt_database.environment_override")


def _default_connect(**parameters: Any) -> Any:
    try:
        import psycopg

        return psycopg.connect(**parameters)
    except Exception:
        _fail("controller_receipt_database.connection_failed")


def _socket_addresses(connection: Any) -> tuple[str, int, str]:
    duplicate: socket.socket | None = None
    try:
        descriptor = connection.pgconn.socket
        if type(descriptor) is not int or descriptor < 0:
            _fail("controller_receipt_database.socket_invalid")
        duplicate = socket.fromfd(descriptor, socket.AF_INET, socket.SOCK_STREAM)
        peer = duplicate.getpeername()
        local = duplicate.getsockname()
        if (
            type(peer) is not tuple
            or len(peer) < 2
            or type(peer[0]) is not str
            or type(peer[1]) is not int
            or type(local) is not tuple
            or not local
            or type(local[0]) is not str
        ):
            _fail("controller_receipt_database.socket_invalid")
        return peer[0], peer[1], local[0]
    except ControllerReceiptV2DatabaseObservationFailure:
        raise
    except Exception:
        _fail("controller_receipt_database.socket_invalid")
    finally:
        if duplicate is not None:
            duplicate.close()


def _connection_parameters(
    plan: _DatabasePlan, policy: Mapping[str, Any], token: str, timeout: int
) -> dict[str, Any]:
    connection = policy["observation"]["connection"]
    transport = policy["observation"]["transport"]
    session = policy["observation"]["session"]
    options = " ".join(
        (
            f"-c default_transaction_read_only={session['default_transaction_read_only']}",
            "-c default_transaction_isolation=repeatable\\ read",
            f"-c statement_timeout={session['statement_timeout']}",
            f"-c lock_timeout={session['lock_timeout']}",
            "-c idle_in_transaction_session_timeout="
            + session["idle_in_transaction_session_timeout"],
            f"-c search_path={session['search_path']}",
        )
    )
    return {
        "application_name": connection["application_name"],
        "autocommit": connection["autocommit"],
        "connect_timeout": timeout,
        "dbname": plan.database_name,
        "gssencmode": "disable",
        "host": plan.target_fqdn,
        "hostaddr": plan.hostaddr,
        "options": options,
        "password": token,
        "port": plan.port,
        "prepare_threshold": None,
        "ssl_max_protocol_version": transport["ssl_max_protocol_version"],
        "ssl_min_protocol_version": transport["ssl_min_protocol_version"],
        "sslcertmode": transport["sslcertmode"],
        "sslmode": transport["sslmode"],
        "sslrootcert": transport["sslrootcert"],
        "user": plan.role_name,
    }


def _configure_transaction(connection: Any, policy: Mapping[str, Any]) -> None:
    try:
        from psycopg import IsolationLevel

        if getattr(connection, "autocommit", None) is not False:
            _fail("controller_receipt_database.transaction_invalid")
        expected = IsolationLevel[policy["driver_isolation_level"]]
        connection.isolation_level = expected
        connection.read_only = policy["driver_read_only"]
        actual = connection.isolation_level
        if (
            type(actual) is not IsolationLevel
            or actual is not expected
            or connection.read_only is not policy["driver_read_only"]
        ):
            _fail("controller_receipt_database.transaction_invalid")
    except ControllerReceiptV2DatabaseObservationFailure:
        raise
    except Exception:
        _fail("controller_receipt_database.transaction_failed")


def _fetch_one(connection: Any, statement: str) -> tuple[Any, ...]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            rows = cursor.fetchmany(2)
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not tuple:
            _fail("controller_receipt_database.query_invalid")
        return rows[0]
    except ControllerReceiptV2DatabaseObservationFailure:
        raise
    except Exception:
        _fail("controller_receipt_database.query_failed")


def _safe_text(value: Any, maximum: int = 256) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and all(0x20 <= ord(character) <= 0x7E for character in value)
    )


def _validated_facts(
    *,
    plan: _DatabasePlan,
    policy: Mapping[str, Any],
    peer_address: str,
    peer_port: int,
    local_address: str,
    rows: Mapping[str, tuple[Any, ...]],
) -> dict[str, Any]:
    try:
        session = rows["session"]
        tls = rows["tls"]
        role = rows["role"]
        settings = policy["observation"]["session"]
        connection = policy["observation"]["connection"]
        role_policy = policy["observation"]["queries"]["role"]
        role_flags = role_policy["required_flags"]
        role_privileges = role_policy["required_privileges"]
        local = ipaddress.ip_address(local_address)
        peer = ipaddress.ip_address(peer_address)
        infrastructure = ipaddress.ip_network(
            plan.infrastructure_subnet_cidr, strict=True
        )
        if type(role[16]) is not str or len(role[16]) > 4096:
            _fail("controller_receipt_database.facts_invalid")
        memberships = json.loads(role[16])
        if type(memberships) is not list or any(
            type(item) is not list
            or len(item) != 4
            or not _safe_text(item[0], 63)
            or any(type(option) is not bool for option in item[1:])
            for item in memberships
        ):
            _fail("controller_receipt_database.facts_invalid")
        if (
            len(session) != 14
            or len(tls) != 4
            or len(role) != 17
            or type(local_address) is not str
            or type(peer) is not ipaddress.IPv4Address
            or type(local) is not ipaddress.IPv4Address
            or peer_address != plan.hostaddr
            or peer_port != plan.port
            or local not in infrastructure
            or local == infrastructure.network_address
            or local == infrastructure.broadcast_address
            or session[:3] != (plan.database_name, plan.role_name, plan.role_name)
            or type(session[3]) is not int
            or session[3] // 10_000 != settings["postgresql_major_version"]
            or session[4] != connection["application_name"]
            or session[5] is not settings["in_recovery"]
            or session[6:14]
            != (
                settings["default_transaction_read_only"],
                settings["default_transaction_isolation"],
                settings["search_path"],
                settings["statement_timeout"],
                settings["lock_timeout"],
                settings["idle_in_transaction_session_timeout"],
                settings["default_transaction_read_only"],
                settings["default_transaction_isolation"],
            )
            or tls[0] is not True
            or tls[1] not in {"TLSv1.2", "TLSv1.3"}
            or not _safe_text(tls[2], 128)
            or type(tls[3]) is not int
            or not 128 <= tls[3] <= 512
            or role[0] != plan.role_name
            or role[1] is not role_flags["rolcanlogin"]
            or role[2] is not role_flags["rolsuper"]
            or role[3] is not role_flags["rolinherit"]
            or role[4] is not role_flags["rolcreaterole"]
            or role[5] is not role_flags["rolcreatedb"]
            or role[6] is not role_flags["rolreplication"]
            or role[7] is not role_flags["rolbypassrls"]
            or type(role[8]) is not int
            or role[8] != role_policy["required_connection_limit"]
            or role[9] is not True
            or role[10] is not role_privileges["database_connect"]
            or role[11] is not role_privileges["database_create"]
            or role[12] is not role_privileges["database_temporary"]
            or role[13] is not role_privileges["public_schema_usage"]
            or role[14] is not role_privileges["public_schema_create"]
            or role[15] is not role_privileges["pg_authid_select"]
            or memberships != role_policy["allowed_memberships"]
        ):
            _fail("controller_receipt_database.facts_invalid")
        return {
            "database": {
                "current_database": session[0],
                "current_user": session[2],
                "session_user": session[1],
            },
            "network": {
                "local_address": local_address,
                "peer_address": peer_address,
                "peer_port": peer_port,
            },
            "role": {
                "can_login": role[1],
                "can_bypass_rls": role[7],
                "can_create_database": role[5],
                "can_create_role": role[4],
                "can_replicate": role[6],
                "connection_limit": role[8],
                "credential_time_valid": role[9],
                "database_connect": role[10],
                "database_create": role[11],
                "database_temporary": role[12],
                "inherits": role[3],
                "memberships": memberships,
                "name": role[0],
                "pg_authid_select": role[15],
                "public_schema_create": role[14],
                "public_schema_usage": role[13],
                "superuser": role[2],
            },
            "session": {
                "application_name": session[4],
                "default_isolation": session[13],
                "default_read_only": session[12],
                "idle_in_transaction_session_timeout": session[11],
                "in_recovery": session[5],
                "isolation": session[7],
                "lock_timeout": session[10],
                "postgresql_major_version": settings["postgresql_major_version"],
                "read_only": session[6],
                "search_path": session[8],
                "server_version_num": session[3],
                "statement_timeout": session[9],
            },
            "tls": {"bits": tls[3], "cipher": tls[2], "version": tls[1]},
        }
    except ControllerReceiptV2DatabaseObservationFailure:
        raise
    except Exception:
        _fail("controller_receipt_database.facts_invalid")


def _validate_facts(
    facts: Any, *, plan: _DatabasePlan, policy: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        document = content._parse_canonical(
            content._canonical(facts), MAXIMUM_POLICY_BYTES
        )
        rows = {
            "session": (
                document["database"]["current_database"],
                document["database"]["session_user"],
                document["database"]["current_user"],
                document["session"]["server_version_num"],
                document["session"]["application_name"],
                document["session"]["in_recovery"],
                document["session"]["read_only"],
                document["session"]["isolation"],
                document["session"]["search_path"],
                document["session"]["statement_timeout"],
                document["session"]["lock_timeout"],
                document["session"]["idle_in_transaction_session_timeout"],
                document["session"]["default_read_only"],
                document["session"]["default_isolation"],
            ),
            "tls": (
                True,
                document["tls"]["version"],
                document["tls"]["cipher"],
                document["tls"]["bits"],
            ),
            "role": (
                document["role"]["name"],
                document["role"]["can_login"],
                document["role"]["superuser"],
                document["role"]["inherits"],
                document["role"]["can_create_role"],
                document["role"]["can_create_database"],
                document["role"]["can_replicate"],
                document["role"]["can_bypass_rls"],
                document["role"]["connection_limit"],
                document["role"]["credential_time_valid"],
                document["role"]["database_connect"],
                document["role"]["database_create"],
                document["role"]["database_temporary"],
                document["role"]["public_schema_usage"],
                document["role"]["public_schema_create"],
                document["role"]["pg_authid_select"],
                content._canonical(document["role"]["memberships"]).decode("utf-8"),
            ),
        }
        expected = _validated_facts(
            plan=plan,
            policy=policy,
            peer_address=document["network"]["peer_address"],
            peer_port=document["network"]["peer_port"],
            local_address=document["network"]["local_address"],
            rows=rows,
        )
        if content._canonical(document) != content._canonical(expected):
            _fail("controller_receipt_database.facts_invalid")
        return document
    except ControllerReceiptV2DatabaseObservationFailure:
        raise
    except Exception:
        _fail("controller_receipt_database.facts_invalid")


def _authority_document(
    *,
    policy: Mapping[str, Any],
    plan: _DatabasePlan,
    facts: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": policy["output"]["kind"],
        "database_observation_policy_id": policy["policy_id"],
        "database_observation_policy_sha256": f"sha256:{POLICY_SHA256}",
        "production_authority_sha256": plan.production_authority_sha256,
        "arm_observation_sha256": plan.arm_observation_sha256,
        "plan_sha256": plan.sha256,
        "rehearsal_id": plan.rehearsal_id,
        "execution_resource_id": plan.execution_resource_id,
        "target": {
            "approved_database_role": plan.role_name,
            "database_name": plan.database_name,
            "executor_private_endpoint_ip": plan.hostaddr,
            "infrastructure_subnet_cidr": plan.infrastructure_subnet_cidr,
            "infrastructure_subnet_id": plan.infrastructure_subnet_id,
            "port": plan.port,
            "target_fqdn": plan.target_fqdn,
        },
        "facts": facts,
        "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": plan.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "claims": dict(policy["claims"]),
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ControllerReceiptV2DatabaseObservationAuthority:
    """Sealed narrow database/TLS/private-route observation."""

    authority_bytes: bytes = dataclass_field(repr=False)
    rehearsal_id: str
    execution_resource_id: str
    observed_at: datetime
    database_role_authenticated: bool
    tls_transport_authenticated: bool
    private_route_reachability_authenticated: bool
    provider_facts_authenticated: bool
    database_mutation_authority: bool
    sanitizer_authority: bool
    writer_identity_use_authenticated: bool
    controller_receipt_exists: bool
    runtime_authority: bool
    executable: bool
    _production_authority: production.ControllerReceiptV2ProductionAuthority = (
        dataclass_field(repr=False, compare=False)
    )
    _arm_observation: arm.AuthenticatedControllerReceiptV2ArmNetworkObservation = (
        dataclass_field(repr=False, compare=False)
    )
    _policy_bytes: bytes = dataclass_field(repr=False, compare=False)
    _facts_bytes: bytes = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_database.authority_unsealed")

    @property
    def authority(self) -> dict[str, Any]:
        if not self._is_authenticated():
            _fail("controller_receipt_database.authority_invalid")
        return content._parse_canonical(self.authority_bytes, MAXIMUM_POLICY_BYTES)

    def _is_authenticated(self) -> bool:
        try:
            policy = validate_controller_receipt_v2_database_observation_policy(
                content._parse_canonical(self._policy_bytes, MAXIMUM_POLICY_BYTES)
            )
            plan = _derive_plan(
                self._production_authority,
                self._arm_observation,
                policy=policy,
                now=self.observed_at,
            )
            facts = _validate_facts(
                content._parse_canonical(self._facts_bytes, MAXIMUM_POLICY_BYTES),
                plan=plan,
                policy=policy,
            )
            expected = _authority_document(
                policy=policy, plan=plan, facts=facts, observed_at=self.observed_at
            )
            return (
                type(self) is ControllerReceiptV2DatabaseObservationAuthority
                and self._seal is _AUTHORITY_SEAL
                and self.authority_bytes == content._canonical(expected)
                and self.rehearsal_id == plan.rehearsal_id
                and self.execution_resource_id == plan.execution_resource_id
                and self.database_role_authenticated is True
                and self.tls_transport_authenticated is True
                and self.private_route_reachability_authenticated is True
                and self.provider_facts_authenticated is False
                and self.database_mutation_authority is False
                and self.sanitizer_authority is False
                and self.writer_identity_use_authenticated is False
                and self.controller_receipt_exists is False
                and self.runtime_authority is False
                and self.executable is False
            )
        except Exception:
            return False


def _mint_authority(
    *,
    production_authority: production.ControllerReceiptV2ProductionAuthority,
    arm_observation: arm.AuthenticatedControllerReceiptV2ArmNetworkObservation,
    policy: Mapping[str, Any],
    plan: _DatabasePlan,
    facts: Mapping[str, Any],
    observed_at: datetime,
) -> ControllerReceiptV2DatabaseObservationAuthority:
    validated_facts = _validate_facts(facts, plan=plan, policy=policy)
    document = _authority_document(
        policy=policy, plan=plan, facts=validated_facts, observed_at=observed_at
    )
    value = object.__new__(ControllerReceiptV2DatabaseObservationAuthority)
    fields = {
        "authority_bytes": content._canonical(document),
        "rehearsal_id": plan.rehearsal_id,
        "execution_resource_id": plan.execution_resource_id,
        "observed_at": observed_at,
        "database_role_authenticated": True,
        "tls_transport_authenticated": True,
        "private_route_reachability_authenticated": True,
        "provider_facts_authenticated": False,
        "database_mutation_authority": False,
        "sanitizer_authority": False,
        "writer_identity_use_authenticated": False,
        "controller_receipt_exists": False,
        "runtime_authority": False,
        "executable": False,
        "_production_authority": production_authority,
        "_arm_observation": arm_observation,
        "_policy_bytes": content._canonical(policy),
        "_facts_bytes": content._canonical(validated_facts),
        "_seal": _AUTHORITY_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    if not value._is_authenticated():
        _fail("controller_receipt_database.authority_invalid")
    return value


class ManagedIdentityControllerReceiptV2DatabaseObserver:
    """One-shot observer whose public call accepts exactly two sealed inputs."""

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
    _connect: Callable[..., Any]
    _deadline_factory: Callable[[int], Any]
    _epoch_clock: Callable[[], float]
    _identity_opener: ingress._HttpOpener
    _lock: threading.Lock
    _monotonic: Callable[[], float]
    _socket_inspector: Callable[[Any], tuple[str, int, str]]
    _used: bool

    def __init__(self) -> None:
        _fail("controller_receipt_database.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        identity_opener: ingress._HttpOpener,
        connect: Callable[..., Any],
        socket_inspector: Callable[[Any], tuple[str, int, str]],
        epoch_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        deadline_factory: Callable[[int], Any] = adapters._wall_clock_deadline,
    ) -> ManagedIdentityControllerReceiptV2DatabaseObserver:
        if (
            cls is not ManagedIdentityControllerReceiptV2DatabaseObserver
            or not all(
                callable(item)
                for item in (
                    clock,
                    connect,
                    socket_inspector,
                    epoch_clock,
                    monotonic,
                    deadline_factory,
                )
            )
            or not callable(getattr(identity_opener, "open", None))
        ):
            _fail("controller_receipt_database.configuration_invalid")
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
        self,
        *,
        production_authority: production.ControllerReceiptV2ProductionAuthority,
        arm_observation: arm.AuthenticatedControllerReceiptV2ArmNetworkObservation,
    ) -> ControllerReceiptV2DatabaseObservationAuthority:
        try:
            return self._observe(production_authority, arm_observation)
        except Exception as error:
            code = (
                error.args[0]
                if type(error) is ControllerReceiptV2DatabaseObservationFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
                else "controller_receipt_database.observation_failed"
            )
        raise ControllerReceiptV2DatabaseObservationFailure(code) from None

    def _observe(
        self,
        production_authority: production.ControllerReceiptV2ProductionAuthority,
        arm_observation: arm.AuthenticatedControllerReceiptV2ArmNetworkObservation,
    ) -> ControllerReceiptV2DatabaseObservationAuthority:
        with self._lock:
            if self._used:
                _fail("controller_receipt_database.already_used")
            self._used = True
        policy = load_controller_receipt_v2_database_observation_policy()
        trusted_utc = ingress._TrustedUtcTimeline(self._clock)
        timeline = _Timeline(self._monotonic)
        started_at = trusted_utc.read()
        transaction = policy["observation"]["transaction"]
        deadline = (
            timeline.read()
            + policy["observation"]["connection"]["maximum_elapsed_seconds"]
        )
        operation_deadline = deadline - transaction["rollback_timeout_seconds"]
        operation_deadline -= transaction["close_timeout_seconds"]
        plan = _derive_plan(
            production_authority,
            arm_observation,
            policy=policy,
            now=started_at,
        )

        def checkpoint(limit: float = operation_deadline) -> datetime:
            now = trusted_utc.read()
            current = _derive_plan(
                production_authority, arm_observation, policy=policy, now=now
            )
            if current != plan or timeline.read() > limit:
                _fail("controller_receipt_database.authority_invalid")
            return now

        _reject_libpq_environment()
        transport = policy["observation"]["transport"]
        try:
            token = runtime_ingress._managed_identity_token(
                client_id=plan.provision_client_id,
                resource=transport["oauth_resource"],
                contract=transport,
                identity_opener=self._identity_opener,
                identity_endpoint=os.environ.get("IDENTITY_ENDPOINT", ""),
                identity_header=os.environ.get("IDENTITY_HEADER", ""),
                epoch_timeline=runtime_ingress._Timeline(
                    self._epoch_clock,
                    "controller_receipt_database.identity_clock_invalid",
                ),
                timeout_seconds=_remaining(
                    timeline,
                    operation_deadline,
                    transport["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
        except ControllerReceiptV2DatabaseObservationFailure:
            raise
        except Exception:
            _fail("controller_receipt_database.identity_failed")
        checkpoint()
        connection: Any = None
        cleanup_failed = False
        rows: dict[str, tuple[Any, ...]] = {}
        peer_address = ""
        peer_port = 0
        local_address = ""
        connection_policy = policy["observation"]["connection"]
        call_timeout = connection_policy["maximum_blocking_call_seconds"]

        def bounded(operation: Callable[[], Any], maximum: int = call_timeout) -> Any:
            with self._deadline_factory(
                _remaining(timeline, operation_deadline, maximum)
            ):
                return operation()

        try:
            connect_timeout = connection_policy["connect_timeout_seconds"]
            parameters = _connection_parameters(plan, policy, token, connect_timeout)
            seconds = _remaining(timeline, operation_deadline, connect_timeout)
            with self._deadline_factory(seconds):
                connection = self._connect(**parameters)
            checkpoint()
            bounded(lambda: _configure_transaction(connection, transaction))
            checkpoint()
            peer_address, peer_port, local_address = bounded(
                lambda: self._socket_inspector(connection)
            )
            checkpoint()
            statements = policy["observation"]["queries"]["statements"]
            for name in policy["observation"]["queries"]["exact_order"]:
                rows[name] = bounded(
                    lambda statement=statements[name]: _fetch_one(connection, statement)
                )
                checkpoint()
        except ControllerReceiptV2DatabaseObservationFailure:
            raise
        except Exception:
            _fail("controller_receipt_database.observation_failed")
        finally:
            token = ""
            if connection is not None:
                for name, cleanup_deadline in (
                    ("rollback", deadline - transaction["close_timeout_seconds"]),
                    ("close", deadline),
                ):
                    maximum = transaction[f"{name}_timeout_seconds"]
                    try:
                        seconds = _remaining(timeline, cleanup_deadline, maximum)
                    except Exception:
                        seconds = maximum
                        cleanup_failed = True
                    try:
                        with self._deadline_factory(seconds):
                            getattr(connection, name)()
                    except Exception:
                        cleanup_failed = True
        if cleanup_failed:
            _fail("controller_receipt_database.cleanup_failed")
        observed_at = checkpoint(deadline)
        facts = _validated_facts(
            plan=plan,
            policy=policy,
            peer_address=peer_address,
            peer_port=peer_port,
            local_address=local_address,
            rows=rows,
        )
        return _mint_authority(
            production_authority=production_authority,
            arm_observation=arm_observation,
            policy=policy,
            plan=plan,
            facts=facts,
            observed_at=observed_at,
        )


def build_controller_receipt_v2_database_observer() -> NoReturn:
    load_controller_receipt_v2_database_observation_policy(executable=True)
    _fail("controller_receipt_database.policy_disabled")


if __name__ == "__main__":
    load_controller_receipt_v2_database_observation_policy(
        executable="--executable" in sys.argv
    )
    print("controller receipt v2 database observation policy valid (dormant)")
