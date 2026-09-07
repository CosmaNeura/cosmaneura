#!/usr/bin/env python3
"""Bind the exact retained runtime role and select a dormant bootstrap design.

This module performs no I/O beyond reading its pinned contract. It exposes no
role or identity value and grants no provider, database, or mutation authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import (
        staging_restore_sanitizer_privileged_global_connection_plan as connection,
    )
    from scripts import (
        staging_restore_sanitizer_target_role_observation_plan as target_plan,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_privileged_global_connection_plan as connection
    import staging_restore_sanitizer_target_role_observation_plan as target_plan


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-bootstrap-binding-plan-contract.json"
)
CONTRACT_SHA256 = "88b3ea9e5ca800ddfc50f10a8d83b4372c9239953877d1b52d2865d561dbdcff"
CONNECTION_PLAN_CONTRACT_SHA256 = (
    "86d3e4ddb0bafffa41438b8f360b84e3ac4a7864604e7372f26e3d6078a15aac"
)
TARGET_PLAN_CONTRACT_SHA256 = (
    "d341d653195b9b3d3d941304205f801d952e3195cd0eefafd0e9d6a9131a0dbb"
)
ACTIVATION_FAILURE = "activation.sanitizer_bootstrap_identity_unavailable"
PLAN_KIND = "sealed-sanitizer-bootstrap-binding-plan-v1"
BOOTSTRAP_MECHANISM = "ephemeral-separate-uami-postgresql-entra-administrator-v1"
BOOTSTRAP_IDENTITY_KEY = "bootstrap"
BOOTSTRAP_IDENTITY_NAME = "cosmaneura-staging-restore-bootstrap-job"
BINDING_DIGEST_DOMAIN = "cosmaneura:staging:sanitizer-runtime-role-binding:v1"
MAXIMUM_CONTRACT_BYTES = 64 * 1024
_ZERO = timedelta(0)
_PLAN_SEAL = object()

PROVISION_IDENTITY_FIELDS = (
    "provision_job_uami_resource_id",
    "provision_job_uami_client_id",
    "provision_job_uami_principal_id",
)
BINDING_DIGEST_FIELDS = (
    "approved_database_role",
    *PROVISION_IDENTITY_FIELDS,
)
LIFECYCLE_ORDER = (
    "authenticate-target-role-and-provision-uami-from-retained-ancestry",
    "authenticate-separate-bootstrap-uami-and-single-use-job-attachment",
    "acquire-server-wide-external-stability-authority",
    "assign-bootstrap-uami-as-exact-target-server-entra-administrator",
    "read-back-exact-sole-entra-administrator-binding",
    "connect-over-private-endpoint-with-explicit-bootstrap-uami-token",
    "provision-exact-runtime-role-mapping-posture-and-privileges",
    "authenticate-post-provision-state-under-held-stability-authorities",
    "rollback-or-commit-as-separately-reviewed-plan-requires-and-close-session",
    "remove-bootstrap-entra-administrator-binding",
    "read-back-bootstrap-authority-absence-and-close-bootstrap-job",
    "handoff-sealed-post-provision-authority",
)

_POSITIVE_CLAIMS = {
    "connection_plan_authenticated": True,
    "exact_database_role_selected": True,
    "provision_job_identity_bound": True,
    "bootstrap_mechanism_selected": True,
}
_NEGATIVE_CLAIMS = dict.fromkeys(
    "bootstrap_identity_bound bootstrap_authority_authenticated "
    "database_connection_authority database_mutation_authority "
    "evidence_publication_authority executable live_database_calls_enabled "
    "provider_calls_permitted role_or_privilege_mutation_authority "
    "runtime_authority sanitizer_authority "
    "server_wide_authorization_stability_authenticated transaction_executed "
    "workflow_integrated".split(),
    False,
)
CLAIMS = {**_POSITIVE_CLAIMS, **_NEGATIVE_CLAIMS}
_BASE_PUBLIC_FIELDS = (
    "kind",
    "bootstrap_binding_plan_sha256",
    "bootstrap_binding_plan_contract_sha256",
    "connection_plan_sha256",
    "target_role_observation_plan_sha256",
    "controller_receipt_sha256",
    "runtime_role_binding_sha256",
    "rehearsal_id",
    "planned_at",
    "expires_at",
    "bootstrap_mechanism",
    "bootstrap_identity_key",
)
PUBLIC_FIELDS = (*_BASE_PUBLIC_FIELDS, *CLAIMS)
PRIVATE_FIELDS = (
    "_connection_plan",
    "_contract_bytes",
    "_proof_sha256",
    "_seal",
)
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "bootstrap_binding_plan.binding_invalid",
        "bootstrap_binding_plan.clock_invalid",
        "bootstrap_binding_plan.clock_regressed",
        "bootstrap_binding_plan.configuration_invalid",
        "bootstrap_binding_plan.connection_plan_invalid",
        "bootstrap_binding_plan.contract_invalid",
        "bootstrap_binding_plan.output_invalid",
        "bootstrap_binding_plan.target_expired",
    }
)


class SanitizerBootstrapBindingPlanFailure(RuntimeError):
    """Bounded refusal without role, identity, target, or credential content."""


def _fail(code: str) -> NoReturn:
    raise SanitizerBootstrapBindingPlanFailure(code) from None


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
        _fail("bootstrap_binding_plan.contract_invalid")


def _parse(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("bootstrap_binding_plan.contract_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("bootstrap_binding_plan.contract_invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "bootstrap_binding_plan.contract_invalid"
            ),
        )
    except SanitizerBootstrapBindingPlanFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("bootstrap_binding_plan.contract_invalid")
    if type(document) is not dict:
        _fail("bootstrap_binding_plan.contract_invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("bootstrap_binding_plan.contract_invalid")
    return _parse(raw, expected_sha256=CONTRACT_SHA256), raw


def _expected_status() -> dict[str, Any]:
    return {
        "contract_validator_implemented": True,
        "retained_runtime_binding_authenticator_implemented": True,
        "bootstrap_mechanism_selected": True,
        "bootstrap_identity_selected": False,
        "bootstrap_job_attachment_implemented": False,
        "bootstrap_control_plane_adapter_implemented": False,
        "bootstrap_database_provisioner_implemented": False,
        "bootstrap_teardown_authenticator_implemented": False,
        "live_database_calls_enabled": False,
        "provider_calls_permitted": False,
        "database_connection_authority": False,
        "database_mutation_authority": False,
        "runtime_authority": False,
        "sanitizer_authority": False,
        "workflow_integrated": False,
        "executable": False,
        "activation_ready": False,
        "public_activation_failure": ACTIVATION_FAILURE,
        "activation_blockers": [
            "bootstrap_identity_exact_ids_unpinned",
            "bootstrap_job_attachment_unimplemented",
            "bootstrap_control_plane_adapter_unimplemented",
            "bootstrap_database_provisioner_unimplemented",
            "bootstrap_teardown_authenticator_unimplemented",
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
        connection_contract = (
            connection.load_privileged_global_connection_plan_contract()
        )
        target_contract = target_plan.load_target_role_observation_plan_contract()
    except Exception:
        _fail("bootstrap_binding_plan.contract_invalid")

    expected_upstream = {
        "privileged_global_connection_plan": {
            "policy_id": connection_contract["policy_id"],
            "sha256": f"sha256:{CONNECTION_PLAN_CONTRACT_SHA256}",
        },
        "target_role_observation_plan": {
            "policy_id": target_contract["policy_id"],
            "sha256": f"sha256:{TARGET_PLAN_CONTRACT_SHA256}",
        },
    }
    expected_input = {
        "only_public_input": "sealed_privileged_global_connection_plan",
        "required_kind": connection.PLAN_KIND,
        "exact_type_seal_process_local_identity_and_retained_ancestry_required": True,
        "current_time_reauthentication_required_at_start_and_end": True,
        "caller_roles_identity_ids_names_tokens_credentials_dsns_connections_"
        "connectors_callbacks_sql_provider_clients_policies_timestamps_or_"
        "runtime_overrides_permitted": False,
    }
    expected_binding = {
        "database_role_source": (
            "connection_plan.retained_target_role_plan.retained_target_binding."
            "approved_database_role"
        ),
        "provision_identity_source": (
            "connection_plan.retained_target_role_plan.retained_database_"
            "observation.production_authority.identity"
        ),
        "required_provision_identity_fields": list(PROVISION_IDENTITY_FIELDS),
        "exact_values_must_match_every_retained_authenticated_ancestor": True,
        "placeholder_empty_or_malformed_values_permitted": False,
        "binding_digest_algorithm": "SHA-256",
        "binding_digest_domain": BINDING_DIGEST_DOMAIN,
        "binding_digest_fields": list(BINDING_DIGEST_FIELDS),
        "raw_database_role_or_identity_values_may_be_public": False,
        "raw_database_role_or_identity_values_retained_outside_existing_sealed_ancestry": False,
    }
    expected_bootstrap = {
        "mechanism": BOOTSTRAP_MECHANISM,
        "identity_source": "future_authenticated_runtime_binding.identities.jobs.bootstrap",
        "identity_name": BOOTSTRAP_IDENTITY_NAME,
        "identity_selected": False,
        "required_identity_fields": ["resource_id", "client_id", "principal_id"],
        "must_be_distinct_from_provision_cleanup_dispatcher_github_and_scribe_identities": True,
        "federated_credentials_permitted": False,
        "persistent_compute_attachment_permitted": False,
        "attached_only_to_single_use_bootstrap_job_required": True,
        "runtime_identity_may_bootstrap": False,
        "password_or_connection_string_permitted": False,
        "default_or_ambient_credential_chain_permitted": False,
        "explicit_managed_identity_client_id_required": True,
        "maximum_database_token_requests": 1,
        "token_retention_permitted": False,
        "public_network_access_permitted": False,
        "provider_or_database_calls_permitted_by_this_plan": False,
        "database_or_role_mutation_permitted_by_this_plan": False,
    }
    expected_lifecycle = {
        "implementation_selected": False,
        "exact_order": list(LIFECYCLE_ORDER),
        "postgresql_administrator_child_resource_name": "ActiveDirectory",
        "administrator_principal_type": "ServicePrincipal",
        "target_server_source": "retained_authenticated_controller_receipt",
        "tenant_source": (
            "retained_authenticated_production_authority.identity.tenant_id"
        ),
        "bootstrap_object_id_source": (
            "future_authenticated_runtime_binding.identities.jobs.bootstrap.principal_id"
        ),
        "provider_create_read_delete_readback_required": True,
        "database_session_must_end_before_provider_teardown": True,
        "bootstrap_authority_absence_required_before_runtime_role_execution": True,
        "external_stability_authority_must_span_first_provider_mutation_through_final_absence_readback": True,
        "retry_pooling_or_ambiguous_outcome_permitted": False,
        "teardown_required_after_success_failure_or_uncertainty": True,
        "runtime_role_mapping_must_use_exact_retained_provision_uami_client_and_principal_ids": True,
        "unlisted_role_acl_database_provider_or_identity_mutation_permitted": False,
    }
    expected_plan = {
        "kind": PLAN_KIND,
        "public_fields": list(PUBLIC_FIELDS),
        "private_fields": list(PRIVATE_FIELDS),
        "copy_pickle_json_serialization_or_mutation_permitted": False,
        "retains_identical_connection_plan_process_local_only": True,
        "retains_no": [
            "raw-database-role",
            "raw-identity-id",
            "bootstrap-authority",
            "access-token",
            "credential",
            "dsn",
            "connection",
            "cursor",
            "connector",
            "callback",
            "sql-text",
            "provider-client",
        ],
    }
    if (
        connection.CONTRACT_SHA256 != CONNECTION_PLAN_CONTRACT_SHA256
        or target_plan.CONTRACT_SHA256 != TARGET_PLAN_CONTRACT_SHA256
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-bootstrap-binding-plan-v1"
        or document.get("closed_world") is not True
        or set(document)
        != {
            "schema_version",
            "policy_id",
            "closed_world",
            "upstream_policies",
            "input",
            "retained_runtime_binding",
            "bootstrap_design",
            "future_lifecycle",
            "plan",
            "claims",
            "status",
        }
        or document.get("upstream_policies") != expected_upstream
        or document.get("input") != expected_input
        or document.get("retained_runtime_binding") != expected_binding
        or document.get("bootstrap_design") != expected_bootstrap
        or document.get("future_lifecycle") != expected_lifecycle
        or document.get("plan") != expected_plan
        or document.get("claims") != CLAIMS
        or document.get("status") != _expected_status()
    ):
        _fail("bootstrap_binding_plan.contract_invalid")


def validate_bootstrap_binding_plan_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact dormant contract and pinned upstreams."""

    if type(contract) is not dict or type(executable) is not bool:
        _fail("bootstrap_binding_plan.contract_invalid")
    template, _raw = _read_contract()
    canonical = _canonical(contract)
    document = _parse(canonical, expected_sha256=hashlib.sha256(canonical).hexdigest())
    if not hmac.compare_digest(_canonical(document), _canonical(template)):
        _fail("bootstrap_binding_plan.contract_invalid")
    _validate_semantics(document)
    if executable:
        _fail(ACTIVATION_FAILURE)
    return document


def load_bootstrap_binding_plan_contract(*, executable: bool = False) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_bootstrap_binding_plan_contract(document, executable=executable)


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("bootstrap_binding_plan.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != _ZERO
        or value.microsecond
        or value.fold
    ):
        _fail("bootstrap_binding_plan.clock_invalid")
    return value


def _uuid(value: object) -> bool:
    if type(value) is not str or value != value.casefold():
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _uami_resource_id(value: object) -> bool:
    if type(value) is not str or "CONFIGURE_" in value:
        return False
    parts = value.split("/")
    return (
        len(parts) == 9
        and parts[0] == ""
        and parts[1].casefold() == "subscriptions"
        and _uuid(parts[2].casefold())
        and parts[3].casefold() == "resourcegroups"
        and bool(parts[4])
        and parts[5].casefold() == "providers"
        and parts[6].casefold() == "microsoft.managedidentity"
        and parts[7].casefold() == "userassignedidentities"
        and bool(parts[8])
    )


def _binding_material(
    value: connection.SanitizerPrivilegedGlobalConnectionPlan, *, now: datetime
) -> tuple[dict[str, Any], target_plan.SanitizerTargetRoleObservationPlan]:
    unexpected_code = "bootstrap_binding_plan.connection_plan_invalid"
    try:
        if (
            type(value) is not connection.SanitizerPrivilegedGlobalConnectionPlan
            or value._seal is not connection._PLAN_SEAL  # noqa: SLF001
            or not value._is_authenticated()  # noqa: SLF001
            or now < value.planned_at
        ):
            _fail("bootstrap_binding_plan.connection_plan_invalid")
        if not now < value.expires_at:
            _fail("bootstrap_binding_plan.target_expired")
        retained = value._target_role_plan  # noqa: SLF001
        if (
            type(retained) is not target_plan.SanitizerTargetRoleObservationPlan
            or retained._seal is not target_plan._PLAN_SEAL  # noqa: SLF001
            or not retained._is_authenticated()  # noqa: SLF001
            or retained is not value._target_role_plan  # noqa: SLF001
            or retained.expires_at != value.expires_at
            or retained.rehearsal_id != value.rehearsal_id
            or retained.observation_plan_sha256
            != value.target_role_observation_plan_sha256
        ):
            _fail("bootstrap_binding_plan.connection_plan_invalid")
        unexpected_code = "bootstrap_binding_plan.binding_invalid"
        target_binding = retained._target_binding  # noqa: SLF001
        observation = retained._database_observation  # noqa: SLF001
        target_projection = target_plan._lineage_projection(  # noqa: SLF001
            target_binding=target_binding,
            database_observation=observation,
            now=now,
        )
        if (
            target_projection["controller_receipt_sha256"]
            != retained.controller_receipt_sha256
            or target_projection["database_observation_sha256"]
            != retained.database_observation_sha256
            or target_projection["rehearsal_id"] != retained.rehearsal_id
        ):
            _fail("bootstrap_binding_plan.binding_invalid")
        identity = observation._production_authority.authority["identity"]  # noqa: SLF001
        role = target_binding.approved_database_role
        material = {
            "approved_database_role": role,
            **{name: identity[name] for name in PROVISION_IDENTITY_FIELDS},
        }
        if (
            target_plan.sanitizer.ROLE_RE.fullmatch(role) is None
            or any(
                type(item) is not str or not item or "CONFIGURE_" in item
                for item in material.values()
            )
            or not _uami_resource_id(material["provision_job_uami_resource_id"])
            or not _uuid(material["provision_job_uami_client_id"])
            or not _uuid(material["provision_job_uami_principal_id"])
            or material["provision_job_uami_client_id"]
            == material["provision_job_uami_principal_id"]
        ):
            _fail("bootstrap_binding_plan.binding_invalid")
        return material, retained
    except SanitizerBootstrapBindingPlanFailure:
        raise
    except Exception:
        _fail(unexpected_code)


def _runtime_role_binding_digest(material: Mapping[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            _canonical({"domain": BINDING_DIGEST_DOMAIN, **dict(material)})
        ).hexdigest()
    )


def _projection(
    value: connection.SanitizerPrivilegedGlobalConnectionPlan, *, now: datetime
) -> dict[str, Any]:
    material, retained = _binding_material(value, now=now)
    return {
        "bootstrap_binding_plan_contract_sha256": f"sha256:{CONTRACT_SHA256}",
        "connection_plan_sha256": value.connection_plan_sha256,
        "target_role_observation_plan_sha256": retained.observation_plan_sha256,
        "controller_receipt_sha256": retained.controller_receipt_sha256,
        "runtime_role_binding_sha256": _runtime_role_binding_digest(material),
        "rehearsal_id": retained.rehearsal_id,
        "planned_at": now,
        "expires_at": retained.expires_at,
        "bootstrap_mechanism": BOOTSTRAP_MECHANISM,
        "bootstrap_identity_key": BOOTSTRAP_IDENTITY_KEY,
        **CLAIMS,
    }


def _serializable(projection: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(projection)
    for name in ("planned_at", "expires_at"):
        value = result.get(name)
        if type(value) is not datetime:
            _fail("bootstrap_binding_plan.output_invalid")
        result[name] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def _plan_digest(projection: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(_serializable(projection))).hexdigest()


def _private_proof(
    *,
    public: Mapping[str, Any],
    value_identity: int,
    retained: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "public": _serializable(public),
                "value_identity": value_identity,
                "seal_identity": id(_PLAN_SEAL),
                "connection_plan_identity": id(retained),
                "connection_plan_type_identity": id(type(retained)),
                "connection_plan_seal_identity": id(retained._seal),  # noqa: SLF001
            }
        )
    ).hexdigest()


class SanitizerBootstrapBindingPlan:
    """Immutable metadata plan retaining its exact connection-plan ancestor."""

    __slots__ = (*PUBLIC_FIELDS, *PRIVATE_FIELDS)

    kind: str
    bootstrap_binding_plan_sha256: str
    bootstrap_binding_plan_contract_sha256: str
    connection_plan_sha256: str
    target_role_observation_plan_sha256: str
    controller_receipt_sha256: str
    runtime_role_binding_sha256: str
    rehearsal_id: str
    planned_at: datetime
    expires_at: datetime
    bootstrap_mechanism: str
    bootstrap_identity_key: str
    connection_plan_authenticated: bool
    exact_database_role_selected: bool
    provision_job_identity_bound: bool
    bootstrap_mechanism_selected: bool
    bootstrap_identity_bound: bool
    bootstrap_authority_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    evidence_publication_authority: bool
    executable: bool
    live_database_calls_enabled: bool
    provider_calls_permitted: bool
    role_or_privilege_mutation_authority: bool
    runtime_authority: bool
    sanitizer_authority: bool
    server_wide_authorization_stability_authenticated: bool
    transaction_executed: bool
    workflow_integrated: bool
    _connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan
    _contract_bytes: bytes
    _proof_sha256: str
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("bootstrap_binding_plan.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("bootstrap_binding_plan.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("bootstrap_binding_plan.output_invalid")

    def __repr__(self) -> str:
        return "<SanitizerBootstrapBindingPlan sealed metadata-only>"

    def __copy__(self) -> NoReturn:
        _fail("bootstrap_binding_plan.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("bootstrap_binding_plan.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("bootstrap_binding_plan.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("bootstrap_binding_plan.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            contract = _parse(self._contract_bytes, expected_sha256=CONTRACT_SHA256)
            _validate_semantics(contract)
            projection = _projection(self._connection_plan, now=self.planned_at)
            public_without_digest = {"kind": PLAN_KIND, **projection}
            public = {
                "kind": PLAN_KIND,
                "bootstrap_binding_plan_sha256": _plan_digest(public_without_digest),
                **projection,
            }
            return (
                type(self) is SanitizerBootstrapBindingPlan
                and self._seal is _PLAN_SEAL
                and tuple(public) == PUBLIC_FIELDS
                and all(getattr(self, name) == item for name, item in public.items())
                and hmac.compare_digest(
                    self._proof_sha256,
                    _private_proof(
                        public=public,
                        value_identity=id(self),
                        retained=self._connection_plan,
                    ),
                )
            )
        except Exception:
            return False


def _mint_plan(
    *,
    retained: connection.SanitizerPrivilegedGlobalConnectionPlan,
    contract_bytes: bytes,
    projection: Mapping[str, Any],
) -> SanitizerBootstrapBindingPlan:
    public_without_digest = {"kind": PLAN_KIND, **projection}
    public = {
        "kind": PLAN_KIND,
        "bootstrap_binding_plan_sha256": _plan_digest(public_without_digest),
        **projection,
    }
    if tuple(public) != PUBLIC_FIELDS:
        _fail("bootstrap_binding_plan.output_invalid")
    value = object.__new__(SanitizerBootstrapBindingPlan)
    for name, item in public.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_connection_plan", retained)
    object.__setattr__(value, "_contract_bytes", bytes(contract_bytes))
    object.__setattr__(
        value,
        "_proof_sha256",
        _private_proof(public=public, value_identity=id(value), retained=retained),
    )
    object.__setattr__(value, "_seal", _PLAN_SEAL)
    if not value._is_authenticated():
        _fail("bootstrap_binding_plan.output_invalid")
    return value


class SanitizerBootstrapBindingPlanner:
    """Pure one-input planner configured only with a reviewed UTC clock."""

    __slots__ = ("_clock", "_last")

    _clock: Callable[[], datetime]
    _last: datetime | None

    def __init__(self) -> None:
        _fail("bootstrap_binding_plan.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("bootstrap_binding_plan.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("bootstrap_binding_plan.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> SanitizerBootstrapBindingPlanner:
        if cls is not SanitizerBootstrapBindingPlanner or not callable(clock):
            _fail("bootstrap_binding_plan.configuration_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_last", None)
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("bootstrap_binding_plan.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def plan(
        self,
        *,
        connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
    ) -> SanitizerBootstrapBindingPlan:
        code = "bootstrap_binding_plan.connection_plan_invalid"
        try:
            if type(self) is not SanitizerBootstrapBindingPlanner:
                _fail("bootstrap_binding_plan.configuration_invalid")
            contract, contract_bytes = _read_contract()
            _validate_semantics(contract)
            started = _projection(connection_plan, now=self._read_clock())
            completed = _projection(connection_plan, now=self._read_clock())
            if any(
                started[name] != completed[name]
                for name in started
                if name != "planned_at"
            ):
                _fail("bootstrap_binding_plan.binding_invalid")
            return _mint_plan(
                retained=connection_plan,
                contract_bytes=contract_bytes,
                projection=completed,
            )
        except Exception as error:
            if (
                type(error) is SanitizerBootstrapBindingPlanFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
            ):
                code = error.args[0]
        raise SanitizerBootstrapBindingPlanFailure(code) from None


def build_sanitizer_bootstrap_binding_planner() -> NoReturn:
    """Refuse public construction until the separate identity exists."""

    load_bootstrap_binding_plan_contract()
    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_bootstrap_binding_plan_contract(executable="--executable" in sys.argv)
    print("sanitizer bootstrap binding plan valid (dormant)")
