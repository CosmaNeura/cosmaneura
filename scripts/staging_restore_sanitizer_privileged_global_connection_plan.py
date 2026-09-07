#!/usr/bin/env python3
"""Dormant connection-sweep plan for privileged/global sanitizer observation.

This module selects the maintenance database, closed database inventory, and
disabled-database handling strategy. It performs no I/O and grants no database
or bootstrap authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import (
        staging_restore_sanitizer_privileged_global_observation_frames as frames,
    )
    from scripts import (
        staging_restore_sanitizer_privileged_global_observer_contract as envelope,
    )
    from scripts import (
        staging_restore_sanitizer_privileged_global_query_grammar as grammar,
    )
    from scripts import (
        staging_restore_sanitizer_target_role_observation_plan as target_plan,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_privileged_global_observation_frames as frames
    import staging_restore_sanitizer_privileged_global_observer_contract as envelope
    import staging_restore_sanitizer_privileged_global_query_grammar as grammar
    import staging_restore_sanitizer_target_role_observation_plan as target_plan


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-connection-plan-contract.json"
)
CONTRACT_SHA256 = "86d3e4ddb0bafffa41438b8f360b84e3ac4a7864604e7372f26e3d6078a15aac"
TARGET_PLAN_CONTRACT_SHA256 = (
    "d341d653195b9b3d3d941304205f801d952e3195cd0eefafd0e9d6a9131a0dbb"
)
OBSERVER_CONTRACT_SHA256 = (
    "040874bb93f30c17168a49284b9255be982f2530e1cf144325ad89ae174a63d0"
)
QUERY_GRAMMAR_CONTRACT_SHA256 = (
    "d6d751a71f87936768f153986f087a89ca5edf4cf1a3dc9f5193a225dbe23bb8"
)
OBSERVATION_FRAMES_CONTRACT_SHA256 = (
    "379f9ea209a1352809b440bce2ab17cc0d049204335eddf9ac4237be1bebf99f"
)
DATABASE_INVENTORY_TEMPLATE_SHA256 = (
    "b37ad59fd60c1a5824f1fcad49ce07e5c5570d0fd60fc13232df46c85a8f8262"
)
ACTIVATION_FAILURE = (
    "activation.sanitizer_privileged_global_database_observer_unimplemented"
)
PLAN_KIND = "sealed-sanitizer-privileged-global-connection-plan-v1"
MAINTENANCE_DATABASE = "postgres"
TARGET_DATABASE = "staging"
EXPECTED_DATABASE_NAMES = (
    "azure_maintenance",
    "azure_sys",
    "postgres",
    "staging",
    "template0",
    "template1",
)
ALLOWED_DISABLED_DATABASE_NAMES = (
    "azure_maintenance",
    "azure_sys",
    "template0",
)
MAXIMUM_CONNECTIONS = 64
MAXIMUM_CONCURRENT_CONNECTIONS = 1
MAXIMUM_TOKEN_REQUESTS = 64
MAXIMUM_CONTRACT_BYTES = 64 * 1024
_ZERO = timedelta(0)
_PLAN_SEAL = object()

_POSITIVE_CLAIMS = {
    "target_role_observation_plan_authenticated": True,
    "maintenance_database_selected": True,
    "disabled_database_surface_observation_strategy_selected": True,
    "connection_sweep_plan_authenticated": True,
}
_NEGATIVE_CLAIMS = dict.fromkeys(
    "bootstrap_authority_authenticated database_connection_authority "
    "database_mutation_authority database_role_state_authenticated "
    "evidence_publication_authority executable live_database_calls_enabled "
    "privileged_global_observation_authenticated provider_calls_permitted "
    "runtime_authority sanitizer_authority "
    "server_wide_authorization_stability_authenticated transaction_executed "
    "workflow_integrated".split(),
    False,
)
CLAIMS = {**_POSITIVE_CLAIMS, **_NEGATIVE_CLAIMS}
_BASE_PUBLIC_FIELDS = (
    "kind",
    "connection_plan_sha256",
    "connection_plan_contract_sha256",
    "target_role_observation_plan_sha256",
    "privileged_global_observer_contract_sha256",
    "query_grammar_contract_sha256",
    "observation_frames_contract_sha256",
    "template_set_sha256",
    "rehearsal_id",
    "planned_at",
    "expires_at",
    "maintenance_database",
    "expected_database_count",
    "maximum_connections",
    "maximum_concurrent_connections",
    "maximum_token_requests",
)
PUBLIC_FIELDS = (*_BASE_PUBLIC_FIELDS, *CLAIMS)
PRIVATE_FIELDS = (
    "_target_role_plan",
    "_contract_bytes",
    "_proof_sha256",
    "_seal",
)
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "privileged_global_connection_plan.clock_invalid",
        "privileged_global_connection_plan.clock_regressed",
        "privileged_global_connection_plan.configuration_invalid",
        "privileged_global_connection_plan.contract_invalid",
        "privileged_global_connection_plan.output_invalid",
        "privileged_global_connection_plan.target_expired",
        "privileged_global_connection_plan.target_plan_invalid",
    }
)


class SanitizerPrivilegedGlobalConnectionPlanFailure(RuntimeError):
    """Bounded refusal without catalog, target, or credential content."""


def _fail(code: str) -> NoReturn:
    raise SanitizerPrivilegedGlobalConnectionPlanFailure(code) from None


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
        _fail("privileged_global_connection_plan.contract_invalid")


def _parse(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("privileged_global_connection_plan.contract_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("privileged_global_connection_plan.contract_invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "privileged_global_connection_plan.contract_invalid"
            ),
        )
    except SanitizerPrivilegedGlobalConnectionPlanFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("privileged_global_connection_plan.contract_invalid")
    if type(document) is not dict:
        _fail("privileged_global_connection_plan.contract_invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("privileged_global_connection_plan.contract_invalid")
    return _parse(raw, expected_sha256=CONTRACT_SHA256), raw


def _expected_status() -> dict[str, Any]:
    return {
        "contract_validator_implemented": True,
        "connection_plan_binder_implemented": True,
        "maintenance_database_selected": True,
        "disabled_database_surface_observation_strategy_selected": True,
        "bootstrap_authority_selected": False,
        "connection_adapter_implemented": False,
        "observer_implemented": False,
        "offline_authenticator_implemented": False,
        "live_database_calls_enabled": False,
        "database_connection_authority": False,
        "database_mutation_authority": False,
        "runtime_authority": False,
        "sanitizer_authority": False,
        "workflow_integrated": False,
        "executable": False,
        "activation_ready": False,
        "public_activation_failure": ACTIVATION_FAILURE,
        "activation_blockers": [
            "exact_database_role_unselected",
            "provision_job_identity_exact_ids_unpinned",
            "bootstrap_authority_unselected",
            "privileged_global_database_observer_unimplemented",
            "privileged_global_offline_authenticator_unimplemented",
            "target_role_and_privileged_global_join_unimplemented",
            "stability_authorities_unimplemented",
            "post_provision_schema_descriptor_unselected",
            "final_adapter_join_unimplemented",
        ],
    }


def _validate_semantics(document: Mapping[str, Any]) -> None:
    try:
        target_document = target_plan.load_target_role_observation_plan_contract()
        observer_document = envelope.load_privileged_global_observer_contract()
        grammar_document = grammar.load_privileged_global_query_grammar_contract()
        frames_document = frames.load_privileged_global_observation_frames_contract()
        templates = {
            item["frame"]: item for item in grammar_document["grammar"]["frames"]
        }
    except Exception:
        _fail("privileged_global_connection_plan.contract_invalid")

    expected_upstream = {
        "target_role_observation_plan": {
            "policy_id": target_document["policy_id"],
            "sha256": f"sha256:{TARGET_PLAN_CONTRACT_SHA256}",
        },
        "privileged_global_observer_envelope": {
            "policy_id": observer_document["policy_id"],
            "sha256": f"sha256:{OBSERVER_CONTRACT_SHA256}",
        },
        "privileged_global_query_grammar": {
            "policy_id": grammar_document["policy_id"],
            "sha256": f"sha256:{QUERY_GRAMMAR_CONTRACT_SHA256}",
        },
        "privileged_global_observation_frames": {
            "policy_id": frames_document["policy_id"],
            "sha256": f"sha256:{OBSERVATION_FRAMES_CONTRACT_SHA256}",
        },
    }
    expected_input = {
        "only_public_input": "sealed_target_role_observation_plan",
        "required_kind": target_plan.PLAN_KIND,
        "exact_type_seal_process_local_identity_and_retained_ancestry_required": True,
        "current_time_reauthentication_required_at_start_and_end": True,
        "caller_database_names_oids_inventory_rows_maps_paths_urls_dsns_hosts_"
        "hostaddrs_ports_roles_identity_ids_tokens_credentials_connectors_callbacks_"
        "sql_policies_timestamps_bootstrap_authorities_stability_authorities_or_"
        "runtime_overrides_permitted": False,
    }
    expected_inventory = {
        "source_frame": "database_inventory",
        "source_template_sha256": f"sha256:{DATABASE_INVENTORY_TEMPLATE_SHA256}",
        "must_be_observed_before_per_database_connections": True,
        "exact_expected_names": list(EXPECTED_DATABASE_NAMES),
        "unexpected_missing_or_duplicate_entry_permitted": False,
        "strictly_increasing_oid_order_required": True,
        "target_database_name_source": (
            "target_role_observation_plan.retained_authenticated_ancestry"
        ),
        "target_database_must_equal": TARGET_DATABASE,
        "target_database_must_be_connectable_and_not_template": True,
        "maximum_entries": grammar_document["resource_bounds"][
            "maximum_database_entries"
        ],
    }
    expected_sweep = {
        "maintenance_database": MAINTENANCE_DATABASE,
        "maintenance_database_must_be_connectable_and_not_template": True,
        "server_once_phase_executes_before_per_database_phase": True,
        "maintenance_connection_reused_for_its_per_database_phase": True,
        "connectable_database_order": "strictly-increasing-database-oid",
        "every_connectable_database_must_be_observed": True,
        "managed_database_connection_or_catalog_failure_permitted": False,
        "maximum_connections": MAXIMUM_CONNECTIONS,
        "maximum_concurrent_connections": MAXIMUM_CONCURRENT_CONNECTIONS,
        "maximum_token_requests": MAXIMUM_TOKEN_REQUESTS,
        "pooling_permitted": False,
        "retry_permitted": False,
        "read_only_repeatable_read_required": True,
        "rollback_and_close_before_next_database_required": True,
        "raw_inventory_or_database_names_may_be_public": False,
    }
    expected_disabled = {
        "strategy": "authenticated-server-scope-inaccessibility",
        "allowed_disabled_database_names": list(ALLOWED_DISABLED_DATABASE_NAMES),
        "template0_must_be_present_disabled_and_template": True,
        "target_postgres_and_template1_must_be_connectable": True,
        "per_database_connection_or_catalog_claim_permitted": False,
        "required_server_once_frames": [
            "database_inventory",
            "role_shared_dependencies",
            "database_acl_sources",
            "database_effective_privileges",
            "runtime_role_sessions",
        ],
        "target_role_must_not_own_database": True,
        "target_role_effective_connect_create_or_temporary_must_all_be_false": True,
        "target_role_active_session_count_must_be_zero": True,
        "target_role_shared_dependency_count_must_be_zero": True,
        "public_or_inherited_access_must_not_restore_effective_access": True,
        "external_server_wide_stability_authority_required_through_sanitizer_close": True,
        "future_enablement_or_database_creation_during_window_permitted": False,
        "disabled_database_application_data_absence_claimed": False,
        "disabled_database_object_catalog_or_system_acl_delta_absence_claimed": False,
    }
    expected_plan = {
        "kind": PLAN_KIND,
        "public_fields": list(PUBLIC_FIELDS),
        "private_fields": list(PRIVATE_FIELDS),
        "copy_pickle_json_serialization_or_mutation_permitted": False,
        "public_runtime_database_inventory_permitted": False,
        "retains_identical_target_role_plan_process_local_only": True,
        "retains_no": [
            "bootstrap-authority",
            "access-token",
            "credential",
            "dsn",
            "connection",
            "cursor",
            "connector",
            "callback",
            "sql-text",
            "database-inventory-row",
            "catalog-row",
        ],
    }
    if (
        target_plan.CONTRACT_SHA256 != TARGET_PLAN_CONTRACT_SHA256
        or envelope.CONTRACT_SHA256 != OBSERVER_CONTRACT_SHA256
        or grammar.CONTRACT_SHA256 != QUERY_GRAMMAR_CONTRACT_SHA256
        or frames.CONTRACT_SHA256 != OBSERVATION_FRAMES_CONTRACT_SHA256
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-privileged-global-connection-plan-v1"
        or document.get("closed_world") is not True
        or set(document)
        != {
            "schema_version",
            "policy_id",
            "closed_world",
            "upstream_policies",
            "input",
            "database_inventory",
            "connection_sweep",
            "disabled_database_strategy",
            "plan",
            "claims",
            "status",
        }
        or document.get("upstream_policies") != expected_upstream
        or document.get("input") != expected_input
        or document.get("database_inventory") != expected_inventory
        or document.get("connection_sweep") != expected_sweep
        or document.get("disabled_database_strategy") != expected_disabled
        or document.get("plan") != expected_plan
        or document.get("claims") != CLAIMS
        or document.get("status") != _expected_status()
        or templates["database_inventory"]["sha256"]
        != f"sha256:{DATABASE_INVENTORY_TEMPLATE_SHA256}"
        or grammar_document["grammar"]["per_database_exact_order"]
        != ["database_object_authority", "system_object_acl_deltas"]
        or grammar_document["execution_envelope"][
            "disabled_database_strategy_remains_unselected"
        ]
        is not True
        or frames_document["status"]["frame_decoder_implemented"] is not True
        or observer_document["future_connection_sweep"]["maintenance_database_selected"]
        is not False
        or observer_document["future_connection_sweep"][
            "disabled_database_surface_observation_strategy_selected"
        ]
        is not False
    ):
        _fail("privileged_global_connection_plan.contract_invalid")


def validate_privileged_global_connection_plan_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact connection-plan contract and pinned dependencies."""

    if type(contract) is not dict or type(executable) is not bool:
        _fail("privileged_global_connection_plan.contract_invalid")
    template, _raw = _read_contract()
    canonical = _canonical(contract)
    document = _parse(canonical, expected_sha256=hashlib.sha256(canonical).hexdigest())
    if not hmac.compare_digest(_canonical(document), _canonical(template)):
        _fail("privileged_global_connection_plan.contract_invalid")
    _validate_semantics(document)
    if executable:
        _fail(ACTIVATION_FAILURE)
    return document


def load_privileged_global_connection_plan_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_privileged_global_connection_plan_contract(
        document, executable=executable
    )


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("privileged_global_connection_plan.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != _ZERO
        or value.microsecond
        or value.fold
    ):
        _fail("privileged_global_connection_plan.clock_invalid")
    return value


def _projection(
    value: target_plan.SanitizerTargetRoleObservationPlan, *, now: datetime
) -> dict[str, Any]:
    try:
        if (
            type(value) is not target_plan.SanitizerTargetRoleObservationPlan
            or value._seal is not target_plan._PLAN_SEAL  # noqa: SLF001
            or not value._is_authenticated()  # noqa: SLF001
            or now < value.planned_at
        ):
            _fail("privileged_global_connection_plan.target_plan_invalid")
        if not now < value.expires_at:
            _fail("privileged_global_connection_plan.target_expired")
        target_binding = value._target_binding  # noqa: SLF001
        if target_binding.database_name != TARGET_DATABASE:
            _fail("privileged_global_connection_plan.target_plan_invalid")
        return {
            "connection_plan_contract_sha256": f"sha256:{CONTRACT_SHA256}",
            "target_role_observation_plan_sha256": value.observation_plan_sha256,
            "privileged_global_observer_contract_sha256": (
                f"sha256:{OBSERVER_CONTRACT_SHA256}"
            ),
            "query_grammar_contract_sha256": (
                f"sha256:{QUERY_GRAMMAR_CONTRACT_SHA256}"
            ),
            "observation_frames_contract_sha256": (
                f"sha256:{OBSERVATION_FRAMES_CONTRACT_SHA256}"
            ),
            "template_set_sha256": f"sha256:{grammar.TEMPLATE_SET_SHA256}",
            "rehearsal_id": value.rehearsal_id,
            "planned_at": now,
            "expires_at": value.expires_at,
            "maintenance_database": MAINTENANCE_DATABASE,
            "expected_database_count": len(EXPECTED_DATABASE_NAMES),
            "maximum_connections": MAXIMUM_CONNECTIONS,
            "maximum_concurrent_connections": MAXIMUM_CONCURRENT_CONNECTIONS,
            "maximum_token_requests": MAXIMUM_TOKEN_REQUESTS,
            **CLAIMS,
        }
    except SanitizerPrivilegedGlobalConnectionPlanFailure:
        raise
    except Exception:
        _fail("privileged_global_connection_plan.target_plan_invalid")


def _serializable(projection: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(projection)
    for name in ("planned_at", "expires_at"):
        value = result.get(name)
        if type(value) is not datetime:
            _fail("privileged_global_connection_plan.output_invalid")
        result[name] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def _plan_digest(projection: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(_canonical(_serializable(projection))).hexdigest()}"


def _private_proof(
    *,
    public: Mapping[str, Any],
    value_identity: int,
    retained: target_plan.SanitizerTargetRoleObservationPlan,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "public": _serializable(public),
                "value_identity": value_identity,
                "seal_identity": id(_PLAN_SEAL),
                "target_role_plan_identity": id(retained),
                "target_role_plan_type_identity": id(type(retained)),
                "target_role_plan_seal_identity": id(retained._seal),  # noqa: SLF001
            }
        )
    ).hexdigest()


class SanitizerPrivilegedGlobalConnectionPlan:
    """Immutable metadata plan retaining its exact target-role plan ancestor."""

    __slots__ = (*PUBLIC_FIELDS, *PRIVATE_FIELDS)

    kind: str
    connection_plan_sha256: str
    connection_plan_contract_sha256: str
    target_role_observation_plan_sha256: str
    privileged_global_observer_contract_sha256: str
    query_grammar_contract_sha256: str
    observation_frames_contract_sha256: str
    template_set_sha256: str
    rehearsal_id: str
    planned_at: datetime
    expires_at: datetime
    maintenance_database: str
    expected_database_count: int
    maximum_connections: int
    maximum_concurrent_connections: int
    maximum_token_requests: int
    target_role_observation_plan_authenticated: bool
    maintenance_database_selected: bool
    disabled_database_surface_observation_strategy_selected: bool
    connection_sweep_plan_authenticated: bool
    bootstrap_authority_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    database_role_state_authenticated: bool
    evidence_publication_authority: bool
    executable: bool
    live_database_calls_enabled: bool
    privileged_global_observation_authenticated: bool
    provider_calls_permitted: bool
    runtime_authority: bool
    sanitizer_authority: bool
    server_wide_authorization_stability_authenticated: bool
    transaction_executed: bool
    workflow_integrated: bool
    _target_role_plan: target_plan.SanitizerTargetRoleObservationPlan
    _contract_bytes: bytes
    _proof_sha256: str
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("privileged_global_connection_plan.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("privileged_global_connection_plan.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("privileged_global_connection_plan.output_invalid")

    def __repr__(self) -> str:
        return "<SanitizerPrivilegedGlobalConnectionPlan sealed metadata-only>"

    def __copy__(self) -> NoReturn:
        _fail("privileged_global_connection_plan.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("privileged_global_connection_plan.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("privileged_global_connection_plan.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("privileged_global_connection_plan.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            contract = _parse(self._contract_bytes, expected_sha256=CONTRACT_SHA256)
            _validate_semantics(contract)
            projection = _projection(self._target_role_plan, now=self.planned_at)
            public_without_digest = {"kind": PLAN_KIND, **projection}
            public = {
                "kind": PLAN_KIND,
                "connection_plan_sha256": _plan_digest(public_without_digest),
                **projection,
            }
            return (
                type(self) is SanitizerPrivilegedGlobalConnectionPlan
                and self._seal is _PLAN_SEAL
                and tuple(public) == PUBLIC_FIELDS
                and all(getattr(self, name) == item for name, item in public.items())
                and hmac.compare_digest(
                    self._proof_sha256,
                    _private_proof(
                        public=public,
                        value_identity=id(self),
                        retained=self._target_role_plan,
                    ),
                )
            )
        except Exception:
            return False


def _mint_plan(
    *,
    retained: target_plan.SanitizerTargetRoleObservationPlan,
    contract_bytes: bytes,
    projection: Mapping[str, Any],
) -> SanitizerPrivilegedGlobalConnectionPlan:
    public_without_digest = {"kind": PLAN_KIND, **projection}
    public = {
        "kind": PLAN_KIND,
        "connection_plan_sha256": _plan_digest(public_without_digest),
        **projection,
    }
    if tuple(public) != PUBLIC_FIELDS:
        _fail("privileged_global_connection_plan.output_invalid")
    value = object.__new__(SanitizerPrivilegedGlobalConnectionPlan)
    for name, item in public.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_target_role_plan", retained)
    object.__setattr__(value, "_contract_bytes", bytes(contract_bytes))
    object.__setattr__(
        value,
        "_proof_sha256",
        _private_proof(public=public, value_identity=id(value), retained=retained),
    )
    object.__setattr__(value, "_seal", _PLAN_SEAL)
    if not value._is_authenticated():
        _fail("privileged_global_connection_plan.output_invalid")
    return value


class SanitizerPrivilegedGlobalConnectionPlanner:
    """Pure one-input planner configured only with a reviewed UTC clock."""

    __slots__ = ("_clock", "_last")

    _clock: Callable[[], datetime]
    _last: datetime | None

    def __init__(self) -> None:
        _fail("privileged_global_connection_plan.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("privileged_global_connection_plan.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("privileged_global_connection_plan.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> SanitizerPrivilegedGlobalConnectionPlanner:
        if cls is not SanitizerPrivilegedGlobalConnectionPlanner or not callable(clock):
            _fail("privileged_global_connection_plan.configuration_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_last", None)
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("privileged_global_connection_plan.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def plan(
        self, *, target_role_plan: target_plan.SanitizerTargetRoleObservationPlan
    ) -> SanitizerPrivilegedGlobalConnectionPlan:
        code = "privileged_global_connection_plan.target_plan_invalid"
        try:
            if type(self) is not SanitizerPrivilegedGlobalConnectionPlanner:
                _fail("privileged_global_connection_plan.configuration_invalid")
            contract, contract_bytes = _read_contract()
            _validate_semantics(contract)
            started = _projection(target_role_plan, now=self._read_clock())
            completed = _projection(target_role_plan, now=self._read_clock())
            if any(
                started[name] != completed[name]
                for name in started
                if name != "planned_at"
            ):
                _fail("privileged_global_connection_plan.target_plan_invalid")
            return _mint_plan(
                retained=target_role_plan,
                contract_bytes=contract_bytes,
                projection=completed,
            )
        except Exception as error:
            if (
                type(error) is SanitizerPrivilegedGlobalConnectionPlanFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
            ):
                code = error.args[0]
        raise SanitizerPrivilegedGlobalConnectionPlanFailure(code) from None


def build_sanitizer_privileged_global_connection_planner() -> NoReturn:
    """Refuse public construction until bootstrap and observer are reviewed."""

    load_privileged_global_connection_plan_contract()
    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_privileged_global_connection_plan_contract(
        executable="--executable" in sys.argv
    )
    print("sanitizer privileged/global connection plan valid (dormant)")
