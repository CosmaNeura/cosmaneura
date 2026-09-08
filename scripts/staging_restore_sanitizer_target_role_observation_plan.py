#!/usr/bin/env python3
"""Dormant target-role database observation plan for the restore sanitizer.

The public planning surface accepts only two exact sealed in-process inputs. It
does not obtain credentials, connect to PostgreSQL, execute SQL, call a
provider, or grant any runtime authority.
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
    from scripts import observe_staging_controller_receipt_v2_database as database
    from scripts import staging_restore_sanitizer as sanitizer
    from scripts import staging_restore_sanitizer_database_capability as capability
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import observe_staging_controller_receipt_v2_database as database
    import staging_restore_sanitizer as sanitizer
    import staging_restore_sanitizer_database_capability as capability


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-target-role-observation-plan-contract.json"
)
CONTRACT_SHA256 = "d341d653195b9b3d3d941304205f801d952e3195cd0eefafd0e9d6a9131a0dbb"
CAPABILITY_CONTRACT_SHA256 = (
    "40684569cbf34d80e144565db73b6f1e81505eb4509c9403797fc0a1114eb98d"
)
SANITIZER_POLICY_SHA256 = (
    "233476e83f8f45419735052a334ea06aa0153d9b463e599c849c1f212e34dad3"
)
DATABASE_OBSERVATION_POLICY_SHA256 = (
    "66dd80c22dd9f21021ec0c4cf604af5ec6afc5fb5d53bd8e946b72abcf8f43bb"
)
ACTIVATION_FAILURE = "activation.sanitizer_target_role_database_observer_unimplemented"
MAXIMUM_CONTRACT_BYTES = 64 * 1024
PLAN_KIND = "sealed-sanitizer-target-role-observation-plan-v1"

_POSITIVE_CLAIMS = {
    "target_binding_authenticated": True,
    "retained_database_observation_authenticated": True,
    "target_role_observation_plan_authenticated": True,
}
_NEGATIVE_CLAIMS = dict.fromkeys(
    "database_connection_authority database_mutation_authority "
    "database_role_state_authenticated effective_session_settings_authenticated "
    "evidence_publication_authority executable live_database_calls_enabled "
    "password_authentication_absence_proven "
    "post_provision_schema_descriptor_authenticated provider_calls_permitted "
    "provider_facts_authenticated provision_job_identity_bound "
    "role_or_privilege_mutation_authority runtime_authority sanitizer_authority "
    "server_wide_authorization_stability_authenticated transaction_committed "
    "transaction_executed workflow_integrated write_path_effects_authenticated".split(),
    False,
)
CLAIMS = {**_POSITIVE_CLAIMS, **_NEGATIVE_CLAIMS}
_BASE_PUBLIC_FIELDS = (
    "kind",
    "observation_plan_sha256",
    "observation_plan_contract_sha256",
    "database_capability_contract_sha256",
    "sanitizer_policy_sha256",
    "database_observation_policy_sha256",
    "controller_receipt_sha256",
    "database_observation_sha256",
    "rehearsal_id",
    "planned_at",
    "expires_at",
)
PUBLIC_FIELDS = (*_BASE_PUBLIC_FIELDS, *CLAIMS)
PRIVATE_FIELDS = (
    "_target_binding",
    "_database_observation",
    "_contract_bytes",
    "_proof_sha256",
    "_seal",
)
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "target_role_observation.authority_invalid",
        "target_role_observation.clock_invalid",
        "target_role_observation.clock_regressed",
        "target_role_observation.configuration_invalid",
        "target_role_observation.contract_invalid",
        "target_role_observation.lineage_invalid",
        "target_role_observation.output_invalid",
        "target_role_observation.target_expired",
    }
)
_PLAN_SEAL = object()
_ZERO = timedelta(0)


class SanitizerTargetRoleObservationFailure(RuntimeError):
    """Bounded refusal without target, credential, or catalog content."""


def _fail(code: str) -> NoReturn:
    raise SanitizerTargetRoleObservationFailure(code) from None


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
        _fail("target_role_observation.contract_invalid")


def _parse(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("target_role_observation.contract_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("target_role_observation.contract_invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "target_role_observation.contract_invalid"
            ),
        )
    except SanitizerTargetRoleObservationFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("target_role_observation.contract_invalid")
    if type(document) is not dict:
        _fail("target_role_observation.contract_invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("target_role_observation.contract_invalid")
    return _parse(raw, expected_sha256=CONTRACT_SHA256), raw


def _validate_semantics(document: Mapping[str, Any]) -> None:
    try:
        capability_contract = capability.load_database_capability_contract()
        sanitizer_policy = sanitizer.load_canonical_policy()
        database_policy = (
            database.load_controller_receipt_v2_database_observation_policy()
        )
        upstream = document["upstream_policies"]
        input_contract = document["input"]
        reauthentication = document["reauthentication"]
        lineage = document["lineage"]
        plan = document["plan"]
        partition = document["requirement_partition"]
        descriptor = document["post_provision_descriptor"]
        status = document["status"]
        claims = document["claims"]
    except (KeyError, TypeError):
        _fail("target_role_observation.contract_invalid")

    expected_upstream = {
        "database_capability": {
            "policy_id": capability_contract["policy_id"],
            "sha256": f"sha256:{CAPABILITY_CONTRACT_SHA256}",
        },
        "sanitizer": {
            "policy_id": sanitizer_policy["policy_id"],
            "sha256": f"sha256:{SANITIZER_POLICY_SHA256}",
        },
        "controller_database_observation": {
            "policy_id": database_policy["policy_id"],
            "sha256": f"sha256:{DATABASE_OBSERVATION_POLICY_SHA256}",
        },
    }
    partition_names = (
        "target_role_observer",
        "privileged_global_observer",
        "external_stability_authorities",
        "post_provision_descriptor_authority",
        "final_adapter_authenticator_join",
    )
    groups = [partition.get(name) for name in partition_names]
    if any(type(group) is not list for group in groups):
        _fail("target_role_observation.contract_invalid")
    flattened = [item for group in groups for item in group]
    requirements = capability_contract["verification"]["must_authenticate"]
    expected_status = {
        "contract_validator_implemented": True,
        "observation_plan_binder_implemented": True,
        "database_observer_implemented": False,
        "offline_authenticator_implemented": False,
        "adapter_join_implemented": False,
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
            "exact_database_role_unselected",
            "provision_job_identity_exact_ids_unpinned",
            "post_provision_schema_descriptor_unselected",
            "target_role_database_observer_unimplemented",
            "privileged_global_observer_unimplemented",
            "stability_authorities_unimplemented",
            "offline_authenticator_unimplemented",
            "adapter_join_unimplemented",
        ],
    }
    if (
        capability.CONTRACT_SHA256 != CAPABILITY_CONTRACT_SHA256
        or sanitizer.CANONICAL_POLICY_SHA256 != SANITIZER_POLICY_SHA256
        or database.POLICY_SHA256 != DATABASE_OBSERVATION_POLICY_SHA256
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-target-role-database-observation-plan-v1"
        or document.get("closed_world") is not True
        or upstream != expected_upstream
        or input_contract
        != {
            "only_public_data_inputs": [
                "target_binding",
                "database_observation",
            ],
            "target_binding_type": (
                "scripts.staging_restore_sanitizer.SanitizerTargetBinding"
            ),
            "database_observation_type": (
                "scripts.observe_staging_controller_receipt_v2_database."
                "ControllerReceiptV2DatabaseObservationAuthority"
            ),
            "exact_type_and_process_local_seal_required": True,
            "database_observation_must_be_identical_retained_ancestor": True,
            "caller_bytes_maps_paths_urls_dsns_hosts_roles_identity_ids_digests_"
            "policies_timestamps_credentials_tokens_connectors_callbacks_sql_or_"
            "runtime_overrides_permitted": False,
        }
        or reauthentication
        != {
            "reviewed_private_clock_only": True,
            "whole_second_utc_required": True,
            "clock_regression_rejected": True,
            "full_target_binding_projection_required_at_start_and_end": True,
            "database_observation_required_at_start_and_end": True,
            "retained_ancestry_identity_required_at_start_and_end": True,
            "plan_may_not_extend_input_expiry": True,
            "placeholder_target_or_identity_values_permitted": False,
        }
        or lineage
        != {
            "retained_database_observation_path": (
                "target_binding._written_receipt._built_receipt."
                "_isolation_inventory._database_observation"
            ),
            "database_observation_digest_path": (
                "target_binding._written_receipt._built_receipt."
                "database_observation_sha256"
            ),
            "cross_bound_fields": [
                "rehearsal_id",
                "execution_resource_id",
                "target_fqdn",
                "target_postgres_server_id",
                "database_name",
                "approved_database_role",
                "controller_receipt_sha256",
                "database_observation_sha256",
            ],
            "content_or_digest_equality_cannot_replace_retained_object_identity": True,
        }
        or plan.get("kind") != PLAN_KIND
        or plan.get("public_fields") != list(PUBLIC_FIELDS)
        or plan.get("private_fields") != list(PRIVATE_FIELDS)
        or plan.get("non_serializable") is not True
        or plan.get("copy_permitted") is not False
        or plan.get("repr_redacted") is not True
        or plan.get("no_capability_material") is not True
        or plan.get("retains_no")
        != [
            "access-token",
            "credential",
            "dsn",
            "connection",
            "cursor",
            "connector",
            "callback",
            "sql-text",
            "detached-or-newly-copied-catalog-row",
            "post-provision-descriptor",
        ]
        or partition.get("source")
        != "database_capability.verification.must_authenticate"
        or partition.get("exact_disjoint_exhaustive_partition_required") is not True
        or len(flattened) != len(set(flattened))
        or set(flattened) != set(requirements)
        or partition["privileged_global_observer"].count("null-password-state") != 1
        or "exact-provision-job-uami-mapping"
        not in partition["privileged_global_observer"]
        or partition["external_stability_authorities"]
        != [
            "server-wide-authorization-state-freeze-from-final-observation-through-transaction"
        ]
        or partition["post_provision_descriptor_authority"]
        != [
            "revision-and-post-provision-schema-descriptor-revalidated-under-held-windows"
        ]
        or descriptor
        != {
            "accepted_as_public_input": False,
            "selected_by_this_plan": False,
            "selection_implemented": False,
            "selection_source": "separately-reviewed-provisioning-output",
            "observer_output_may_select_expected_digest": False,
            "pre_provision_descriptor_substitution_permitted": False,
        }
        or claims != CLAIMS
        or status != expected_status
        or capability_contract["status"]["database_state_authenticator_implemented"]
        is not False
        or capability_contract["status"][
            "post_provision_schema_descriptor_observation_implemented"
        ]
        is not False
        or capability_contract["schema"]["descriptor"][
            "post_provision_descriptor_selected"
        ]
        is not False
        or capability_contract["schema"]["descriptor"]["post_provision"][
            "pre_provision_sha256_substitution_permitted"
        ]
        is not False
    ):
        _fail("target_role_observation.contract_invalid")


def validate_target_role_observation_plan_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact offline plan contract and its pinned dependencies."""

    if type(contract) is not dict or type(executable) is not bool:
        _fail("target_role_observation.contract_invalid")
    template, _raw = _read_contract()
    document = _parse(
        _canonical(contract),
        expected_sha256=hashlib.sha256(_canonical(contract)).hexdigest(),
    )
    if not hmac.compare_digest(_canonical(document), _canonical(template)):
        _fail("target_role_observation.contract_invalid")
    _validate_semantics(document)
    if executable:
        _fail(ACTIVATION_FAILURE)
    return document


def load_target_role_observation_plan_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_target_role_observation_plan_contract(
        document,
        executable=executable,
    )


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("target_role_observation.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != _ZERO
        or value.microsecond
        or value.fold
    ):
        _fail("target_role_observation.clock_invalid")
    return value


def _placeholder(value: object) -> bool:
    return type(value) is not str or not value or "CONFIGURE_" in value


def _lineage_projection(
    *,
    target_binding: sanitizer.SanitizerTargetBinding,
    database_observation: database.ControllerReceiptV2DatabaseObservationAuthority,
    now: datetime,
) -> dict[str, Any]:
    try:
        if (
            type(target_binding) is not sanitizer.SanitizerTargetBinding
            or getattr(target_binding, "_seal", None)
            is not sanitizer._TARGET_BINDING_SEAL  # noqa: SLF001
            or type(database_observation)
            is not database.ControllerReceiptV2DatabaseObservationAuthority
            or getattr(database_observation, "_seal", None)
            is not database._AUTHORITY_SEAL  # noqa: SLF001
            or not target_binding._is_bound()  # noqa: SLF001
            or not database_observation._is_authenticated()  # noqa: SLF001
            or target_binding.bound_at > now
            or database_observation.observed_at > target_binding.bound_at
        ):
            _fail("target_role_observation.authority_invalid")
        sanitizer_policy = sanitizer.load_canonical_policy()
        expected_target = sanitizer._binding_projection(  # noqa: SLF001
            written_receipt=target_binding._written_receipt,  # noqa: SLF001
            policy=sanitizer_policy,
            now=now,
        )
        if any(
            getattr(target_binding, name) != value
            for name, value in expected_target.items()
            if name != "bound_at"
        ):
            _fail("target_role_observation.authority_invalid")
        written = target_binding._written_receipt  # noqa: SLF001
        built = written._built_receipt  # noqa: SLF001
        retained = built._isolation_inventory._database_observation  # noqa: SLF001
        if retained is not database_observation:
            _fail("target_role_observation.lineage_invalid")
        observation_document = database_observation.authority
        arm_document = database_observation._arm_observation.authority  # noqa: SLF001
        production_document = database_observation._production_authority.authority  # noqa: SLF001
        database_policy = (
            database.load_controller_receipt_v2_database_observation_policy()
        )
        database_plan = database._derive_plan(  # noqa: SLF001
            database_observation._production_authority,  # noqa: SLF001
            database_observation._arm_observation,  # noqa: SLF001
            policy=database_policy,
            now=now,
        )
        target = observation_document["target"]
        identity = production_document["identity"]
        observation_sha256 = sanitizer.receipt_content._digest(
            database_observation.authority_bytes
        )
        observation_expires_at = sanitizer.receipt_content._utc(
            observation_document["expires_at"]
        )
        expires_at = min(target_binding.expires_at, observation_expires_at)
        if (
            built._isolation_inventory._database_observation  # noqa: SLF001
            is not database_observation
            or built.database_observation_sha256 != observation_sha256
            or built.execution_resource_id != database_observation.execution_resource_id
            or target_binding.rehearsal_id != database_observation.rehearsal_id
            or target_binding.rehearsal_id != built.rehearsal_id
            or target_binding.receipt_sha256 != built.receipt_sha256
            or target_binding.target_fqdn != target["target_fqdn"]
            or target_binding.database_name != target["database_name"]
            or target_binding.approved_database_role != target["approved_database_role"]
            or target_binding.target_postgres_server_id
            != arm_document["target"]["target_postgres_server_id"]
            or target_binding.target_fqdn != database_plan.target_fqdn
            or target_binding.database_name != database_plan.database_name
            or target_binding.approved_database_role != database_plan.role_name
            or identity["provision_job_uami_client_id"]
            != database_plan.provision_client_id
            or any(
                _placeholder(value)
                for value in (
                    target_binding.target_fqdn,
                    target_binding.target_postgres_server_id,
                    target_binding.database_name,
                    target_binding.approved_database_role,
                    identity["provision_job_uami_resource_id"],
                    identity["provision_job_uami_client_id"],
                    identity["provision_job_uami_principal_id"],
                )
            )
            or not now < expires_at
        ):
            _fail("target_role_observation.authority_invalid")
        return {
            "observation_plan_contract_sha256": f"sha256:{CONTRACT_SHA256}",
            "database_capability_contract_sha256": (
                f"sha256:{CAPABILITY_CONTRACT_SHA256}"
            ),
            "sanitizer_policy_sha256": f"sha256:{SANITIZER_POLICY_SHA256}",
            "database_observation_policy_sha256": (
                f"sha256:{DATABASE_OBSERVATION_POLICY_SHA256}"
            ),
            "controller_receipt_sha256": target_binding.receipt_sha256,
            "database_observation_sha256": observation_sha256,
            "rehearsal_id": target_binding.rehearsal_id,
            "planned_at": now,
            "expires_at": expires_at,
            **CLAIMS,
        }
    except SanitizerTargetRoleObservationFailure:
        raise
    except sanitizer.SanitizerFailure as error:
        if error.args == ("sanitizer_binding.expired",):
            _fail("target_role_observation.target_expired")
        _fail("target_role_observation.authority_invalid")
    except Exception:
        _fail("target_role_observation.authority_invalid")


def _serializable(projection: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(projection)
    for name in ("planned_at", "expires_at"):
        value = result.get(name)
        if type(value) is not datetime:
            _fail("target_role_observation.output_invalid")
        result[name] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def _plan_digest(projection: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(_canonical(_serializable(projection))).hexdigest()}"


def _private_proof(
    *,
    public: Mapping[str, Any],
    plan_identity: int,
    target_binding: sanitizer.SanitizerTargetBinding,
    database_observation: database.ControllerReceiptV2DatabaseObservationAuthority,
) -> str:
    material = {
        "public": _serializable(public),
        "plan_identity": plan_identity,
        "target_binding_identity": id(target_binding),
        "target_binding_type_identity": id(type(target_binding)),
        "target_binding_seal_identity": id(target_binding._seal),  # noqa: SLF001
        "database_observation_identity": id(database_observation),
        "database_observation_type_identity": id(type(database_observation)),
        "database_observation_seal_identity": id(database_observation._seal),  # noqa: SLF001
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


class SanitizerTargetRoleObservationPlan:
    """Sealed metadata-only plan retaining exact in-process lineage."""

    __slots__ = (
        *PUBLIC_FIELDS,
        "_target_binding",
        "_database_observation",
        "_contract_bytes",
        "_proof_sha256",
        "_seal",
    )

    kind: str
    observation_plan_sha256: str
    observation_plan_contract_sha256: str
    database_capability_contract_sha256: str
    sanitizer_policy_sha256: str
    database_observation_policy_sha256: str
    controller_receipt_sha256: str
    database_observation_sha256: str
    rehearsal_id: str
    planned_at: datetime
    expires_at: datetime
    target_binding_authenticated: bool
    retained_database_observation_authenticated: bool
    target_role_observation_plan_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    database_role_state_authenticated: bool
    effective_session_settings_authenticated: bool
    evidence_publication_authority: bool
    executable: bool
    live_database_calls_enabled: bool
    password_authentication_absence_proven: bool
    post_provision_schema_descriptor_authenticated: bool
    provider_calls_permitted: bool
    provider_facts_authenticated: bool
    provision_job_identity_bound: bool
    role_or_privilege_mutation_authority: bool
    runtime_authority: bool
    sanitizer_authority: bool
    server_wide_authorization_stability_authenticated: bool
    transaction_committed: bool
    transaction_executed: bool
    workflow_integrated: bool
    write_path_effects_authenticated: bool
    _target_binding: sanitizer.SanitizerTargetBinding
    _database_observation: database.ControllerReceiptV2DatabaseObservationAuthority
    _contract_bytes: bytes
    _proof_sha256: str
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("target_role_observation.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("target_role_observation.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("target_role_observation.output_invalid")

    def __repr__(self) -> str:
        return "<SanitizerTargetRoleObservationPlan sealed metadata-only>"

    def __copy__(self) -> NoReturn:
        _fail("target_role_observation.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("target_role_observation.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("target_role_observation.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("target_role_observation.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            contract = _parse(
                self._contract_bytes,
                expected_sha256=CONTRACT_SHA256,
            )
            _validate_semantics(contract)
            projection = _lineage_projection(
                target_binding=self._target_binding,
                database_observation=self._database_observation,
                now=self.planned_at,
            )
            public_without_digest = {"kind": PLAN_KIND, **projection}
            expected_digest = _plan_digest(public_without_digest)
            public = {
                "kind": PLAN_KIND,
                "observation_plan_sha256": expected_digest,
                **projection,
            }
            actual_public = {name: getattr(self, name) for name in PUBLIC_FIELDS}
            return (
                type(self) is SanitizerTargetRoleObservationPlan
                and self._seal is _PLAN_SEAL
                and tuple(public) == PUBLIC_FIELDS
                and all(
                    type(actual_public[name]) is type(value)
                    and actual_public[name] == value
                    for name, value in public.items()
                )
                and hmac.compare_digest(
                    self._proof_sha256,
                    _private_proof(
                        public=actual_public,
                        plan_identity=id(self),
                        target_binding=self._target_binding,
                        database_observation=self._database_observation,
                    ),
                )
            )
        except Exception:
            return False


def _mint_plan(
    *,
    target_binding: sanitizer.SanitizerTargetBinding,
    database_observation: database.ControllerReceiptV2DatabaseObservationAuthority,
    contract_bytes: bytes,
    projection: Mapping[str, Any],
) -> SanitizerTargetRoleObservationPlan:
    public_without_digest = {"kind": PLAN_KIND, **projection}
    public = {
        "kind": PLAN_KIND,
        "observation_plan_sha256": _plan_digest(public_without_digest),
        **projection,
    }
    if tuple(public) != PUBLIC_FIELDS:
        _fail("target_role_observation.output_invalid")
    value = object.__new__(SanitizerTargetRoleObservationPlan)
    for name, item in public.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_target_binding", target_binding)
    object.__setattr__(value, "_database_observation", database_observation)
    object.__setattr__(value, "_contract_bytes", bytes(contract_bytes))
    object.__setattr__(
        value,
        "_proof_sha256",
        _private_proof(
            public=public,
            plan_identity=id(value),
            target_binding=target_binding,
            database_observation=database_observation,
        ),
    )
    object.__setattr__(value, "_seal", _PLAN_SEAL)
    if not value._is_authenticated():
        _fail("target_role_observation.output_invalid")
    return value


class SanitizerTargetRoleObservationPlanner:
    """Pure planner configured only with a reviewed whole-second UTC clock."""

    __slots__ = ("_clock", "_last")

    def __init__(self) -> None:
        _fail("target_role_observation.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("target_role_observation.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("target_role_observation.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> SanitizerTargetRoleObservationPlanner:
        if cls is not SanitizerTargetRoleObservationPlanner or not callable(clock):
            _fail("target_role_observation.configuration_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_last", None)
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("target_role_observation.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def plan(
        self,
        *,
        target_binding: sanitizer.SanitizerTargetBinding,
        database_observation: database.ControllerReceiptV2DatabaseObservationAuthority,
    ) -> SanitizerTargetRoleObservationPlan:
        code = "target_role_observation.authority_invalid"
        try:
            if type(self) is not SanitizerTargetRoleObservationPlanner:
                _fail("target_role_observation.configuration_invalid")
            contract, contract_bytes = _read_contract()
            _validate_semantics(contract)
            started_at = self._read_clock()
            started = _lineage_projection(
                target_binding=target_binding,
                database_observation=database_observation,
                now=started_at,
            )
            completed_at = self._read_clock()
            completed = _lineage_projection(
                target_binding=target_binding,
                database_observation=database_observation,
                now=completed_at,
            )
            stable_names = set(started).difference({"planned_at"})
            if any(started[name] != completed[name] for name in stable_names):
                _fail("target_role_observation.authority_invalid")
            return _mint_plan(
                target_binding=target_binding,
                database_observation=database_observation,
                contract_bytes=contract_bytes,
                projection=completed,
            )
        except Exception as error:
            if (
                type(error) is SanitizerTargetRoleObservationFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
            ):
                code = error.args[0]
        raise SanitizerTargetRoleObservationFailure(code) from None


def build_sanitizer_target_role_observation_planner() -> NoReturn:
    """Refuse public construction until the one-shot observer exists."""

    load_target_role_observation_plan_contract()
    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_target_role_observation_plan_contract(executable="--executable" in sys.argv)
    print("sanitizer target-role observation plan contract valid (dormant)")
