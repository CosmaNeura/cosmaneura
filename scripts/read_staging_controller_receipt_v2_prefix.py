#!/usr/bin/env python3
"""Dormant exact intent/submission reader for receipt-v2 production."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, NoReturn

try:
    from scripts import read_staging_controller_receipt_v2 as ingress
    from scripts import read_staging_runtime_record_material as runtime_ingress
    from scripts import staging_restore_launch_material as launch_material
    from scripts import staging_restore_runtime_adapters as runtime_adapters
    from scripts import validate_staging_controller_receipt_v2 as content
    from scripts import validate_staging_controller_receipt_v2_production as production
    from scripts import validate_staging_restore_launch_binding as launch
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import read_staging_controller_receipt_v2 as ingress
    import read_staging_runtime_record_material as runtime_ingress
    import staging_restore_launch_material as launch_material
    import staging_restore_runtime_adapters as runtime_adapters
    import validate_staging_controller_receipt_v2 as content
    import validate_staging_controller_receipt_v2_production as production
    import validate_staging_restore_launch_binding as launch
    import validate_staging_restore_runtime_binding as runtime_binding


Failure = production.ControllerReceiptV2ProductionFailure


def _fail(code: str) -> NoReturn:
    raise Failure(code) from None


class ManagedIdentityPrivateControllerReceiptV2PrefixReader:
    """One-shot, provision-only reader whose public call has no selectors."""

    __slots__ = (
        "_blob_opener",
        "_clock",
        "_controller_policy",
        "_deadline_factory",
        "_epoch_clock",
        "_identity_opener",
        "_lock",
        "_monotonic",
        "_production_policy",
        "_runtime_policy",
        "_used",
    )
    _lock: threading.Lock
    _used: bool

    def __init__(self) -> None:
        _fail("controller_receipt_production.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls,
        *,
        runtime_policy: Any,
        controller_policy: Any,
        production_policy: Any,
        clock: Callable[[], datetime],
        identity_opener: ingress._HttpOpener | None = None,
        blob_opener: ingress._HttpOpener | None = None,
        epoch_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        deadline_factory: Callable[[int], Any] = runtime_adapters._wall_clock_deadline,
    ) -> ManagedIdentityPrivateControllerReceiptV2PrefixReader:
        if (
            cls is not ManagedIdentityPrivateControllerReceiptV2PrefixReader
            or not callable(clock)
            or any(
                opener is not None and not callable(getattr(opener, "open", None))
                for opener in (identity_opener, blob_opener)
            )
            or not callable(epoch_clock)
            or not callable(monotonic)
            or not callable(deadline_factory)
        ):
            _fail("controller_receipt_production.configuration_invalid")
        runtime, controller, policy = production._validate_context(
            runtime_policy, controller_policy, production_policy
        )
        value = object.__new__(cls)
        object.__setattr__(
            value, "_runtime_policy", runtime_binding._canonical(runtime)
        )
        object.__setattr__(value, "_controller_policy", content._canonical(controller))
        object.__setattr__(value, "_production_policy", content._canonical(policy))
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_identity_opener", identity_opener)
        object.__setattr__(value, "_blob_opener", blob_opener)
        object.__setattr__(value, "_epoch_clock", epoch_clock)
        object.__setattr__(value, "_monotonic", monotonic)
        object.__setattr__(value, "_deadline_factory", deadline_factory)
        object.__setattr__(value, "_lock", threading.Lock())
        object.__setattr__(value, "_used", False)
        return value

    def _claim_use(self) -> None:
        with self._lock:
            if self._used:
                _fail("controller_receipt_production.already_used")
            self._used = True

    def _validated_policies(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return production._validate_context(
            runtime_binding._parse_policy(self._runtime_policy),
            content._parse_canonical(
                self._controller_policy, content.MAXIMUM_POLICY_BYTES
            ),
            content._parse_canonical(
                self._production_policy, production.MAXIMUM_POLICY_BYTES
            ),
        )

    def _checkpoint(
        self,
        material: launch_material.ProvisionLaunchMaterial,
        expected: ingress._AuthoritySnapshot,
        trusted_utc: ingress._TrustedUtcTimeline,
        timeline: ingress._Timeline,
        deadline: float,
    ) -> tuple[datetime, dict[str, Any], dict[str, Any], dict[str, Any]]:
        runtime, controller, policy = self._validated_policies()
        now = trusted_utc.read()
        snapshot, _record, _signed, _validated_runtime = (
            production._validate_launch_material(
                runtime, controller, material, validation_now=now
            )
        )
        if snapshot != expected:
            _fail("controller_receipt_production.launch_invalid")
        if (
            os.environ.get(launch.EXECUTION_NAME_ENV) != expected.execution_name
            or now < expected.authenticated_receipt_observed_at
            or now >= expected.launch_expires_at
        ):
            _fail("controller_receipt_production.launch_invalid")
        if timeline.read() > deadline:
            _fail("controller_receipt_production.deadline")
        return now, runtime, controller, policy

    def read_and_bind(
        self, *, launch_material_value: launch_material.ProvisionLaunchMaterial
    ) -> production.ControllerReceiptV2ProductionAuthority:
        """Read the fixed prefix and bind it to one sealed provision launch."""

        self._claim_use()
        runtime, controller, policy = self._validated_policies()
        timeline = ingress._Timeline(self._monotonic)
        trusted_utc = ingress._TrustedUtcTimeline(self._clock)
        initial_now = trusted_utc.read()
        snapshot, _record, _signed, _validated_runtime = (
            production._validate_launch_material(
                runtime,
                controller,
                launch_material_value,
                validation_now=initial_now,
            )
        )
        source = controller["record_chain_source"]
        retrieval = source["retrieval"]
        prefix_policy = policy["prefix_source"]
        if (
            snapshot.action != production.PRODUCTION_ACTION
            or prefix_policy["record_order"] != list(production.PREFIX_RECORD_KINDS)
            or retrieval["maximum_storage_token_requests"] != 1
            or retrieval["maximum_record_get_requests"] < 2
            or prefix_policy["maximum_total_http_exchanges"] != 3
        ):
            _fail("controller_receipt_production.policy_invalid")
        prefix_uris = tuple(
            (kind, uri)
            for kind, uri in snapshot.object_uris
            if kind in production.PREFIX_RECORD_KINDS
        )
        if (
            tuple(kind for kind, _uri in prefix_uris) != production.PREFIX_RECORD_KINDS
            or len(prefix_uris) != prefix_policy["maximum_record_get_requests"]
        ):
            _fail("controller_receipt_production.policy_invalid")
        deadline = timeline.read() + retrieval["maximum_elapsed_seconds"]
        identity_endpoint = os.environ.get("IDENTITY_ENDPOINT", "")
        identity_header = os.environ.get("IDENTITY_HEADER", "")
        try:
            identity_opener = (
                self._identity_opener or runtime_ingress._default_identity_opener()
            )
            blob_opener = self._blob_opener or runtime_ingress._default_service_opener(
                retrieval["runtime_ca_bundle_path"]
            )
            epoch_timeline = runtime_ingress._Timeline(
                self._epoch_clock,
                "controller_receipt_production.identity_clock_invalid",
            )
        except Exception:
            _fail("controller_receipt_production.configuration_invalid")

        def checkpoint() -> tuple[
            datetime, dict[str, Any], dict[str, Any], dict[str, Any]
        ]:
            return self._checkpoint(
                launch_material_value,
                snapshot,
                trusted_utc,
                timeline,
                deadline,
            )

        checkpoint()
        try:
            token = runtime_ingress._managed_identity_token(
                client_id=snapshot.reader_identity_client_id,
                resource=source["oauth_resource"],
                contract=retrieval,
                identity_opener=identity_opener,
                identity_endpoint=identity_endpoint,
                identity_header=identity_header,
                epoch_timeline=epoch_timeline,
                timeout_seconds=ingress._remaining_timeout(
                    timeline,
                    deadline=deadline,
                    maximum=retrieval["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
        except Failure:
            raise
        except Exception:
            _fail("controller_receipt_production.identity_failed")
        checkpoint()
        objects: list[ingress._AuthenticatedPrivateWormObject] = []
        for kind, uri in prefix_uris:
            now, _runtime, current_controller, _policy = checkpoint()
            try:
                observed = ingress._read_worm_object(
                    opener=blob_opener,
                    token=token,
                    kind=kind,
                    uri=uri,
                    source=source,
                    now=now,
                    maximum_clock_skew_seconds=current_controller["freshness"][
                        "maximum_clock_skew_seconds"
                    ],
                    timeout_seconds=ingress._remaining_timeout(
                        timeline,
                        deadline=deadline,
                        maximum=retrieval["maximum_request_timeout_seconds"],
                    ),
                    deadline_factory=self._deadline_factory,
                )
            except Exception:
                _fail("controller_receipt_production.record_invalid")
            objects.append(observed)
            checkpoint()
        validated_at, runtime, controller, policy = checkpoint()
        try:
            prefix = production._mint_prefix_source(
                runtime=runtime,
                controller=controller,
                material=launch_material_value,
                objects=tuple(objects),
                validated_at=validated_at,
            )
            result = production._bind_production_authority(
                runtime=runtime,
                controller=controller,
                production=policy,
                prefix=prefix,
                material=launch_material_value,
            )
        except Failure:
            raise
        except Exception:
            _fail("controller_receipt_production.authority_invalid")
        checkpoint()
        if not result._is_authenticated():
            _fail("controller_receipt_production.authority_invalid")
        return result


def build_private_controller_receipt_v2_prefix_reader(
    *, clock: Callable[[], datetime]
) -> ManagedIdentityPrivateControllerReceiptV2PrefixReader:
    """Load only repository-pinned policy; dormant flags currently refuse."""

    try:
        policy = production.load_controller_receipt_v2_production_policy(
            executable=True
        )
        runtime = runtime_binding.load_policy(executable=True)
        controller = content.load_controller_receipt_v2_policy()
    except Exception:
        _fail("controller_receipt_production.policy_disabled")
    reader_type = ManagedIdentityPrivateControllerReceiptV2PrefixReader
    return reader_type._from_reviewed_configuration(
        runtime_policy=runtime,
        controller_policy=controller,
        production_policy=policy,
        clock=clock,
    )
