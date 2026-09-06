#!/usr/bin/env python3
"""Pure dormant builder for one controller-receipt-v2 document.

The only data input is a sealed isolation-inventory authority.  That authority
retains the complete in-process production, ARM, database, and inventory chain.
This module performs no provider, database, storage, subprocess, or workflow
operation and does not claim that the resulting bytes were durably written.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import observe_staging_controller_receipt_v2_arm as arm
    from scripts import observe_staging_controller_receipt_v2_database as database
    from scripts import (
        observe_staging_controller_receipt_v2_isolation_inventory as isolation,
    )
    from scripts import validate_staging_controller_receipt_v2 as content
    from scripts import validate_staging_controller_receipt_v2_production as production
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` import compatibility.
    import observe_staging_controller_receipt_v2_arm as arm
    import observe_staging_controller_receipt_v2_database as database
    import observe_staging_controller_receipt_v2_isolation_inventory as isolation
    import validate_staging_controller_receipt_v2 as content
    import validate_staging_controller_receipt_v2_production as production
    import validate_staging_restore_runtime_binding as runtime_binding


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/controller-receipt-v2-builder-policy.json"
)
POLICY_SHA256 = "147297b0781abdc5cded8cfd5e0118d364ccf334d234da3bbe0aa79ac097a0dc"
MAXIMUM_POLICY_BYTES = 64 * 1024

_BUILT_SEAL = object()
_FAILURE_CODES = frozenset(
    {
        "controller_receipt_builder.authority_invalid",
        "controller_receipt_builder.build_failed",
        "controller_receipt_builder.clock_invalid",
        "controller_receipt_builder.configuration_invalid",
        "controller_receipt_builder.expired",
        "controller_receipt_builder.policy_disabled",
        "controller_receipt_builder.policy_invalid",
    }
)


class ControllerReceiptV2BuildFailure(RuntimeError):
    """Bounded refusal that never includes authority or record content."""


def _fail(code: str) -> NoReturn:
    raise ControllerReceiptV2BuildFailure(code) from None


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
            _fail("controller_receipt_builder.policy_invalid")
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
            _fail("controller_receipt_builder.policy_invalid")
    except ControllerReceiptV2BuildFailure:
        raise
    except OSError:
        _fail("controller_receipt_builder.policy_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("controller_receipt_builder.policy_invalid")
    if type(document) is not dict:
        _fail("controller_receipt_builder.policy_invalid")
    return document


def _expected_upstream_policies() -> dict[str, dict[str, str]]:
    return {
        "arm_observation": {
            "policy_id": "cosmaneura-staging-controller-receipt-v2-arm-observation-v1",
            "sha256": f"sha256:{arm.POLICY_SHA256}",
        },
        "controller_receipt_v2": {
            "policy_id": "cosmaneura-staging-controller-receipt-v2",
            "sha256": f"sha256:{content.POLICY_SHA256}",
        },
        "database_observation": {
            "policy_id": (
                "cosmaneura-staging-controller-receipt-v2-database-observation-v1"
            ),
            "sha256": f"sha256:{database.POLICY_SHA256}",
        },
        "isolation_inventory": {
            "policy_id": (
                "cosmaneura-staging-controller-receipt-v2-isolation-inventory-"
                "observation-v1"
            ),
            "sha256": f"sha256:{isolation.POLICY_SHA256}",
        },
        "production_authority": {
            "policy_id": "cosmaneura-staging-controller-receipt-v2-production-v1",
            "sha256": f"sha256:{production.POLICY_SHA256}",
        },
        "restore_runtime_binding": {
            "policy_id": "cosmaneura-staging-restore-runtime-binding-v1",
            "sha256": f"sha256:{runtime_binding.CANONICAL_POLICY_SHA256}",
        },
    }


def validate_controller_receipt_v2_builder_policy(
    value: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact digest-pinned dormant builder policy."""

    template = _read_policy()
    try:
        document = json.loads(content._canonical(value))
    except Exception:
        _fail("controller_receipt_builder.policy_invalid")
    expected_activation = {
        "consumer_binding_implemented": False,
        "controller_receipt_builder_implemented": True,
        "controller_receipt_writer_implemented": False,
        "executable": False,
        "live_provider_calls_enabled": False,
        "workflow_integrated": False,
    }
    expected_claims = {
        "arm_network_facts_authenticated": True,
        "blob_or_redis_copy_absence_proven": False,
        "content_built_and_validated": True,
        "controller_receipt_exists": False,
        "create_only_behavior_proven": False,
        "database_mutation_authority": False,
        "database_role_authenticated": True,
        "executable": False,
        "execution_history_authenticated": False,
        "full_three_record_source_authenticated": False,
        "intent_submission_source_authenticated": True,
        "point_in_time_isolation_inventory_authenticated": True,
        "private_route_reachability_authenticated": True,
        "provider_facts_authenticated": False,
        "runtime_authority": False,
        "sanitizer_authority": False,
        "tls_transport_authenticated": True,
        "workflow_integrated": False,
        "writer_identity_use_authenticated": False,
    }
    build = document.get("build", {})
    input_policy = document.get("input", {})
    output = document.get("output", {})
    if (
        type(executable) is not bool
        or content._canonical(document) != content._canonical(template)
        or set(document)
        != {
            "activation",
            "activation_blockers",
            "activation_ready",
            "build",
            "claims",
            "closed_world",
            "input",
            "output",
            "policy_id",
            "schema_version",
            "upstream_policies",
        }
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("policy_id")
        != "cosmaneura-staging-controller-receipt-v2-builder-v1"
        or document.get("closed_world") is not True
        or document.get("activation_ready") is not False
        or document.get("activation") != expected_activation
        or not isinstance(document.get("activation_blockers"), list)
        or not document["activation_blockers"]
        or document.get("claims") != expected_claims
        or document.get("upstream_policies") != _expected_upstream_policies()
        or input_policy
        != {
            "caller_fields_digests_pass_paths_policies_selectors_or_times_permitted": False,
            "clock_source": "reviewed-configuration-only",
            "kind": (
                "authenticated-controller-receipt-v2-isolation-inventory-observation-v1"
            ),
            "only_public_data_input": "isolation_inventory",
        }
        or output
        != {
            "kind": "built-controller-receipt-v2-content-v1",
            "persisted_receipt_bytes_preserve_provider_provenance": False,
            "private_in_process_isolation_authority_retained": True,
            "unknown_fields_permitted": False,
        }
        or build.get("authority_chain_order")
        != [
            "production_authority",
            "arm_observation",
            "database_observation",
            "isolation_inventory",
        ]
        or build.get("record_chain_order")
        != ["intent", "submission", "controller_receipt"]
        or build.get("expires_at_sources")
        != [
            "created_at_plus_controller_maximum_receipt_lifetime",
            "isolation_inventory_expires_at",
            "database_observation_expires_at",
            "arm_observation_expires_at",
            "launch_expires_at",
            "cleanup_not_after",
        ]
        or type(build.get("maximum_isolation_inventory_age_seconds")) is not int
        or build["maximum_isolation_inventory_age_seconds"] != 60
        or build.get("serialization") != "canonical-json-no-trailing-newline"
        or any(
            build.get(name) is not True
            for name in (
                "full_ancestor_reauthentication_required",
                "inventory_regeneration_from_retained_exact_responses_required",
                "shared_controller_content_validator_required",
                "trusted_clock_whole_second_utc_required",
            )
        )
    ):
        _fail("controller_receipt_builder.policy_invalid")
    if executable:
        _fail("controller_receipt_builder.policy_disabled")
    return document


def load_controller_receipt_v2_builder_policy(
    *, executable: bool = False
) -> dict[str, Any]:
    return validate_controller_receipt_v2_builder_policy(
        _read_policy(), executable=executable
    )


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ordered_times(values: tuple[datetime, ...]) -> bool:
    return all(left <= right for left, right in zip(values, values[1:], strict=False))


def _record_chain_sha256(
    intent_sha256: str, submission_sha256: str, receipt_sha256: str
) -> str:
    return content._digest(
        content._canonical(
            [
                {"kind": "intent", "sha256": intent_sha256},
                {"kind": "submission", "sha256": submission_sha256},
                {"kind": "controller_receipt", "sha256": receipt_sha256},
            ]
        )
    )


def _authority_chain_sha256(
    *,
    policy: Mapping[str, Any],
    production_sha256: str,
    arm_sha256: str,
    database_sha256: str,
    inventory_sha256: str,
) -> str:
    pins = policy["upstream_policies"]
    authorities = [
        {
            "kind": "production_authority",
            "policy_sha256": pins["production_authority"]["sha256"],
            "sha256": production_sha256,
        },
        {
            "kind": "arm_observation",
            "policy_sha256": pins["arm_observation"]["sha256"],
            "sha256": arm_sha256,
        },
        {
            "kind": "database_observation",
            "policy_sha256": pins["database_observation"]["sha256"],
            "sha256": database_sha256,
        },
        {
            "kind": "isolation_inventory",
            "policy_sha256": pins["isolation_inventory"]["sha256"],
            "sha256": inventory_sha256,
        },
    ]
    if [item["kind"] for item in authorities] != policy["build"][
        "authority_chain_order"
    ]:
        _fail("controller_receipt_builder.policy_invalid")
    return content._digest(
        content._canonical(
            {
                "authorities": authorities,
                "controller_policy_sha256": pins["controller_receipt_v2"]["sha256"],
                "runtime_policy_sha256": pins["restore_runtime_binding"]["sha256"],
            }
        )
    )


def _build_projection(
    authority: isolation.ControllerReceiptV2IsolationInventoryAuthority,
    *,
    policy: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    try:
        if (
            type(authority)
            is not isolation.ControllerReceiptV2IsolationInventoryAuthority
            or not authority._is_authenticated()
        ):
            _fail("controller_receipt_builder.authority_invalid")
        database_authority = authority._database_observation
        production_authority = database_authority._production_authority
        arm_observation = database_authority._arm_observation
        if (
            type(database_authority)
            is not database.ControllerReceiptV2DatabaseObservationAuthority
            or type(production_authority)
            is not production.ControllerReceiptV2ProductionAuthority
            or type(arm_observation)
            is not arm.AuthenticatedControllerReceiptV2ArmNetworkObservation
            or arm_observation._production_authority is not production_authority
            or not database_authority._is_authenticated()
            or not arm_observation._is_authenticated()
            or not production_authority._is_authenticated()
        ):
            _fail("controller_receipt_builder.authority_invalid")

        runtime, controller, production_policy, production_document = (
            arm._fresh_context(production_authority, now)
        )
        arm_policy = arm.validate_controller_receipt_v2_arm_observation_policy(
            content._parse_canonical(
                arm_observation._policy_bytes, arm.MAXIMUM_POLICY_BYTES
            )
        )
        database_policy = (
            database.validate_controller_receipt_v2_database_observation_policy(
                content._parse_canonical(
                    database_authority._policy_bytes, database.MAXIMUM_POLICY_BYTES
                )
            )
        )
        inventory_policy = (
            isolation.validate_controller_receipt_v2_isolation_inventory_policy(
                content._parse_canonical(
                    authority._policy_bytes, isolation.MAXIMUM_POLICY_BYTES
                )
            )
        )
        pins = policy["upstream_policies"]
        if (
            pins != _expected_upstream_policies()
            or production_policy["policy_id"]
            != pins["production_authority"]["policy_id"]
            or arm_policy["policy_id"] != pins["arm_observation"]["policy_id"]
            or database_policy["policy_id"] != pins["database_observation"]["policy_id"]
            or inventory_policy["policy_id"] != pins["isolation_inventory"]["policy_id"]
            or controller["policy_id"] != pins["controller_receipt_v2"]["policy_id"]
            or runtime["policy_id"] != pins["restore_runtime_binding"]["policy_id"]
        ):
            _fail("controller_receipt_builder.authority_invalid")

        database_plan = database._derive_plan(
            production_authority,
            arm_observation,
            policy=database_policy,
            now=now,
        )
        inventory_plan = isolation._derive_plan(
            database_authority, policy=inventory_policy, now=now
        )
        arm_document = arm_observation.authority
        database_document = database_authority.authority
        facts, response_manifest = isolation._fact_document(
            authority._responses, inventory_plan, inventory_policy
        )
        inventory_document = isolation._authority_document(
            policy=inventory_policy,
            plan=inventory_plan,
            responses=authority._responses,
            started_at=authority._started_at,
            observed_at=authority.observed_at,
        )
        if (
            authority.authority_bytes != content._canonical(inventory_document)
            or inventory_document["facts"] != facts
            or inventory_document["response_manifest"] != response_manifest
            or inventory_document["request_plan_sha256"] != inventory_plan.sha256
            or database_document["plan_sha256"] != database_plan.sha256
        ):
            _fail("controller_receipt_builder.authority_invalid")

        production_sha256 = content._digest(production_authority.authority_bytes)
        arm_sha256 = content._digest(arm_observation.authority_bytes)
        database_sha256 = content._digest(database_authority.authority_bytes)
        inventory_authority_sha256 = content._digest(authority.authority_bytes)
        policy_binding_sha256 = content._policy_binding_digest(controller)
        runtime_policy_binding_sha256 = runtime_binding._policy_binding_digest(runtime)
        shared_rehearsal_id = production_authority.rehearsal_id
        shared_execution = production_authority.execution_binding
        if (
            any(
                item != shared_rehearsal_id
                for item in (
                    arm_observation.rehearsal_id,
                    database_authority.rehearsal_id,
                    authority.rehearsal_id,
                    production_document["authorization"]["rehearsal_id"],
                    arm_document["rehearsal_id"],
                    database_document["rehearsal_id"],
                    inventory_document["rehearsal_id"],
                )
            )
            or any(
                item != shared_execution
                for item in (
                    arm_observation.execution_resource_id,
                    database_authority.execution_resource_id,
                    authority.execution_resource_id,
                    production_document["launch"]["execution_resource_id"],
                    arm_document["execution_resource_id"],
                    database_document["execution_resource_id"],
                    inventory_document["execution_resource_id"],
                )
            )
            or arm_document["production_authority_sha256"] != production_sha256
            or database_document["production_authority_sha256"] != production_sha256
            or database_document["arm_observation_sha256"] != arm_sha256
            or inventory_document["production_authority_sha256"] != production_sha256
            or inventory_document["arm_observation_sha256"] != arm_sha256
            or inventory_document["database_observation_sha256"] != database_sha256
            or arm_document["controller_policy_binding_sha256"] != policy_binding_sha256
            or inventory_document["controller_policy_binding_sha256"]
            != policy_binding_sha256
            or arm_document["runtime_policy_binding_sha256"]
            != runtime_policy_binding_sha256
            or inventory_document["runtime_policy_binding_sha256"]
            != runtime_policy_binding_sha256
        ):
            _fail("controller_receipt_builder.authority_invalid")

        arm_response_times = tuple(
            item.observed_at for item in arm_observation._responses
        )
        inventory_response_times = tuple(
            item.observed_at for item in authority._responses
        )
        causal_times = (
            production_authority.validated_at,
            arm_observation._started_at,
            *arm_response_times,
            arm_observation.observed_at,
            database_authority.observed_at,
            authority._started_at,
            *inventory_response_times,
            authority.observed_at,
            now,
        )
        maximum_age = timedelta(
            seconds=policy["build"]["maximum_isolation_inventory_age_seconds"]
        )
        if (
            not _ordered_times(causal_times)
            or now - authority.observed_at > maximum_age
            or production_authority.validated_at
            != content._utc(production_document["validated_at"])
        ):
            _fail("controller_receipt_builder.authority_invalid")

        arm_target = arm_document["target"]
        database_target = database_document["target"]
        runtime_values = {
            "target_fqdn": arm_target["target_fqdn"],
            "approved_database_role": database_target["approved_database_role"],
            **{
                name: arm_target[name]
                for name in content.RUNTIME_FIELDS
                if name not in {"target_fqdn", "approved_database_role"}
            },
        }
        if (
            database_target["target_fqdn"] != runtime_values["target_fqdn"]
            or database_target["approved_database_role"]
            != controller["fixed_bindings"]["approved_database_role"]
            or arm_target["target_postgres_server_id"]
            != production_document["request"]["target_server_resource_id"]
        ):
            _fail("controller_receipt_builder.authority_invalid")
        resource_set_sha256 = content._digest(content._canonical(runtime_values))
        target = {**runtime_values, "resource_set_sha256": resource_set_sha256}
        isolation_values = {name: facts[name] for name in content.ISOLATION_FIELDS}

        cleanup = content._utc(production_document["request"]["cleanup_not_after"])
        launch_expiry = content._utc(production_document["launch"]["launch_expires_at"])
        expiry = min(
            now
            + timedelta(
                seconds=controller["freshness"]["maximum_receipt_lifetime_seconds"]
            ),
            content._utc(inventory_document["expires_at"]),
            content._utc(database_document["expires_at"]),
            content._utc(arm_document["expires_at"]),
            launch_expiry,
            cleanup,
        )
        if not now < expiry:
            _fail("controller_receipt_builder.expired")
        upstream = controller["upstream_policies"]
        receipt = {
            "schema_version": controller["receipt"]["schema_version"],
            "kind": controller["receipt"]["kind"],
            "policy_id": controller["policy_id"],
            "policy_binding_sha256": policy_binding_sha256,
            "repository": controller["fixed_bindings"]["repository"],
            "policy_pins": {
                "safe_submit_policy_id": upstream["safe_submit"]["policy_id"],
                "safe_submit_policy_sha256": upstream["safe_submit"]["sha256"],
                "restore_envelope_policy_id": upstream["restore_envelope"]["policy_id"],
                "restore_envelope_policy_sha256": upstream["restore_envelope"][
                    "sha256"
                ],
            },
            "identity": {
                name: production_document["identity"][name]
                for name in content.IDENTITY_FIELDS
            },
            "authorization": {
                name: production_document["authorization"][name]
                for name in content.AUTHORIZATION_FIELDS
            },
            "launch": {
                name: production_document["launch"][name]
                for name in content.LAUNCH_FIELDS
            },
            "source": {
                name: production_document["source"][name]
                for name in content.SOURCE_FIELDS
            },
            "target": target,
            "restore_observed_at": arm_document["observed_at"],
            "created_at": _timestamp(now),
            "expires_at": _timestamp(expiry),
            "cleanup_not_after": _timestamp(cleanup),
            "isolation": isolation_values,
            "pass": True,
        }
        receipt_bytes = content._canonical(receipt)
        validated = content.validate_controller_receipt_v2_content(
            controller,
            intent_bytes=production_authority.intent_bytes,
            submission_bytes=production_authority.submission_bytes,
            receipt_bytes=receipt_bytes,
            clock=lambda: now,
        )
        receipt_sha256 = content._digest(receipt_bytes)
        if (
            validated.receipt_bytes != receipt_bytes
            or validated.receipt_sha256 != receipt_sha256
            or validated.policy_binding_sha256 != policy_binding_sha256
            or validated.provenance_authenticated is not False
            or any(
                getattr(validated, name) != runtime_values[name]
                for name in content.RUNTIME_FIELDS
            )
        ):
            _fail("controller_receipt_builder.build_failed")
        return {
            "intent_bytes": bytes(production_authority.intent_bytes),
            "submission_bytes": bytes(production_authority.submission_bytes),
            "receipt_bytes": receipt_bytes,
            "intent_sha256": production_authority.intent_sha256,
            "submission_sha256": production_authority.submission_sha256,
            "receipt_sha256": receipt_sha256,
            "record_chain_sha256": _record_chain_sha256(
                production_authority.intent_sha256,
                production_authority.submission_sha256,
                receipt_sha256,
            ),
            "authority_chain_sha256": _authority_chain_sha256(
                policy=policy,
                production_sha256=production_sha256,
                arm_sha256=arm_sha256,
                database_sha256=database_sha256,
                inventory_sha256=inventory_authority_sha256,
            ),
            "production_authority_sha256": production_sha256,
            "arm_observation_sha256": arm_sha256,
            "database_observation_sha256": database_sha256,
            "isolation_inventory_authority_sha256": inventory_authority_sha256,
            "known_resource_inventory_sha256": facts["known_resource_inventory_sha256"],
            "policy_binding_sha256": policy_binding_sha256,
            "runtime_policy_binding_sha256": runtime_policy_binding_sha256,
            "resource_set_sha256": resource_set_sha256,
            "rehearsal_id": shared_rehearsal_id,
            "execution_resource_id": shared_execution,
            "restore_observed_at": content._utc(receipt["restore_observed_at"]),
            "created_at": now,
            "expires_at": expiry,
            "cleanup_not_after": cleanup,
        }
    except ControllerReceiptV2BuildFailure:
        raise
    except Exception:
        _fail("controller_receipt_builder.authority_invalid")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class BuiltControllerReceiptV2:
    """Canonical built bytes plus their still-private in-process provenance."""

    intent_bytes: bytes = dataclass_field(repr=False)
    submission_bytes: bytes = dataclass_field(repr=False)
    receipt_bytes: bytes = dataclass_field(repr=False)
    intent_sha256: str
    submission_sha256: str
    receipt_sha256: str
    record_chain_sha256: str
    authority_chain_sha256: str
    production_authority_sha256: str
    arm_observation_sha256: str
    database_observation_sha256: str
    isolation_inventory_authority_sha256: str
    known_resource_inventory_sha256: str
    policy_binding_sha256: str
    runtime_policy_binding_sha256: str
    resource_set_sha256: str
    rehearsal_id: str
    execution_resource_id: str
    restore_observed_at: datetime
    created_at: datetime
    expires_at: datetime
    cleanup_not_after: datetime
    content_built_and_validated: bool
    intent_submission_source_authenticated: bool
    arm_network_facts_authenticated: bool
    database_role_authenticated: bool
    tls_transport_authenticated: bool
    private_route_reachability_authenticated: bool
    point_in_time_isolation_inventory_authenticated: bool
    controller_receipt_exists: bool
    full_three_record_source_authenticated: bool
    create_only_behavior_proven: bool
    writer_identity_use_authenticated: bool
    provider_facts_authenticated: bool
    execution_history_authenticated: bool
    blob_or_redis_copy_absence_proven: bool
    database_mutation_authority: bool
    sanitizer_authority: bool
    runtime_authority: bool
    executable: bool
    workflow_integrated: bool
    _isolation_inventory: isolation.ControllerReceiptV2IsolationInventoryAuthority = (
        dataclass_field(repr=False, compare=False)
    )
    _builder_policy_bytes: bytes = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_builder.authority_invalid")

    def _is_built(self) -> bool:
        try:
            policy = validate_controller_receipt_v2_builder_policy(
                content._parse_canonical(
                    self._builder_policy_bytes, MAXIMUM_POLICY_BYTES
                )
            )
            expected = _build_projection(
                self._isolation_inventory, policy=policy, now=self.created_at
            )
            claims = policy["claims"]
            return (
                type(self) is BuiltControllerReceiptV2
                and self._seal is _BUILT_SEAL
                and all(
                    getattr(self, name) == value for name, value in expected.items()
                )
                and all(getattr(self, name) is value for name, value in claims.items())
            )
        except Exception:
            return False


def _mint_built(
    *,
    authority: isolation.ControllerReceiptV2IsolationInventoryAuthority,
    policy: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> BuiltControllerReceiptV2:
    value = object.__new__(BuiltControllerReceiptV2)
    for name, item in projection.items():
        object.__setattr__(value, name, item)
    for name, item in policy["claims"].items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_isolation_inventory", authority)
    object.__setattr__(value, "_builder_policy_bytes", content._canonical(policy))
    object.__setattr__(value, "_seal", _BUILT_SEAL)
    if not value._is_built():
        _fail("controller_receipt_builder.build_failed")
    return value


class ControllerReceiptV2Builder:
    """Pure builder configured only with a reviewed trusted UTC clock."""

    __slots__ = ("_clock",)
    _clock: Callable[[], datetime]

    def __init__(self) -> None:
        _fail("controller_receipt_builder.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> ControllerReceiptV2Builder:
        if cls is not ControllerReceiptV2Builder or not callable(clock):
            _fail("controller_receipt_builder.configuration_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        return value

    def build(
        self,
        *,
        isolation_inventory: isolation.ControllerReceiptV2IsolationInventoryAuthority,
    ) -> BuiltControllerReceiptV2:
        try:
            if type(self) is not ControllerReceiptV2Builder or not callable(
                getattr(self, "_clock", None)
            ):
                _fail("controller_receipt_builder.configuration_invalid")
            policy = load_controller_receipt_v2_builder_policy()
            try:
                now = content._trusted_now(self._clock)
            except Exception:
                _fail("controller_receipt_builder.clock_invalid")
            projection = _build_projection(isolation_inventory, policy=policy, now=now)
            return _mint_built(
                authority=isolation_inventory,
                policy=policy,
                projection=projection,
            )
        except Exception as error:
            code = (
                error.args[0]
                if type(error) is ControllerReceiptV2BuildFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
                else "controller_receipt_builder.build_failed"
            )
        raise ControllerReceiptV2BuildFailure(code) from None
