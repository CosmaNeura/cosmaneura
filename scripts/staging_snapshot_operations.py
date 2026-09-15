"""Typed snapshot I/O composition for the future reviewed HUMAN launcher.

No launcher, plan/handoff acceptance, authority minting or resource mutation.
The caller must FIRST verify protected source and exact independent reviews,
bind the actual plan/handoff/artifacts, open the human-owned encrypted area,
prepare the configured session, and obtain both real console confirmations.
Its artifact callback must re-read those exact retained public artifacts under
the existing timer; a no-op or substituted callback is NEVER live acceptance.
Only fixture tests may invoke this adapter from an agent.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from staging_snapshot_azure import SnapshotAzure
from staging_snapshot_capture import (
    Archive,
    CaptureInputs,
    HumanClosure,
    Operations,
    Rule,
    rules_by_name,
    valid_sha,
)
from staging_snapshot_cli import OwnedAzureCLI
from staging_snapshot_configuration import (
    BoundConfiguration,
    ConfiguredSource,
    private_new_file,
)
from staging_snapshot_console import SnapshotConsole
from staging_snapshot_native_files import NativeFiles, identity
from staging_snapshot_pg_io import (
    BoundedChildren,
    DumpResult,
    PayloadBinding,
    capture_deadline,
    capture_with_verified_payload,
    require_capture_process,
)
from staging_snapshot_postgresql_runtime import PostgreSQLRuntime
from staging_snapshot_public_ip import SnapshotPublicIP
from staging_snapshot_runtime import SnapshotRuntime
from staging_snapshot_session import SnapshotSession
from staging_snapshot_workspace import BoundArea, SnapshotWorkspace

if TYPE_CHECKING:
    from staging_snapshot_bootstrap import SourceRuntimeBinding

IMAGE = "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:475908dbb4c6820f91ca6ccd626312ef4f7591c8555923f02949520fcd4208d7"
IDENTITY_KEYS = (
    "configured_username_sha256",
    "current_token_oid_sha256",
    "home_account_id_sha256",
    "exact_arm_token_sha256",
)
METADATA_NAME = "capture-metadata.json"


class SnapshotOperationsRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise SnapshotOperationsRefused("snapshot_operations_refused")


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def deadline_for(timeout: float, maximum: int) -> float:
    require(
        type(timeout) in {int, float}
        and math.isfinite(timeout)
        and 0 < timeout <= maximum
    )
    return time.monotonic() + timeout


class SnapshotOperations(Operations):
    def __init__(
        self,
        inputs: CaptureInputs,
        source: SourceRuntimeBinding,
        area: BoundArea,
        session: SnapshotSession,
        console: SnapshotConsole,
        identity_evidence: dict[str, object],
        application_binding_sha256: str,
        artifact_revalidate: Callable[[], None],
    ):
        require_capture_process()
        require(type(inputs) is CaptureInputs and type(identity_evidence) is dict)
        inputs.validate(datetime.now(UTC))
        require(callable(artifact_revalidate) and valid_sha(application_binding_sha256))
        require(
            hashlib.sha256(canonical(identity_evidence)).hexdigest()
            == inputs.identity_evidence_sha256
        )
        require(
            all(
                valid_sha(cast(str, identity_evidence.get(key)))
                for key in IDENTITY_KEYS
            )
        )
        require(
            identity_evidence.get("configured_home_account_linked_to_exact_arm_token")
            is True
        )
        require(identity_evidence.get("authenticated_same_token_arm_read") is True)
        require(
            identity_evidence.get("authenticated_same_token_read_required") is False
        )
        require(identity_evidence.get("human_apply_authorized") is False)
        self._inputs, self._source, self._area = inputs, source, area
        self._session, self._console = session, console
        self._identity = {key: identity_evidence[key] for key in IDENTITY_KEYS}
        self._application_sha = application_binding_sha256
        self._artifact_revalidate = artifact_revalidate
        self._children: BoundedChildren | None = None
        self._archive: Archive | None = None
        self._baseline: dict[str, Rule] | None = None
        self._window: datetime | None = None
        self._capture_attempted = self._published = self._closure_verified = False
        self._closure: HumanClosure | None = None
        self._types()
        self._azure = SnapshotAzure(session)
        self._ip = SnapshotPublicIP(source.files, self._local_checks)

    def _types(self) -> None:
        require_capture_process()
        # The source loader executes as __main__; importing another bootstrap
        # would create a second class/module and is deliberately not accepted.
        require(
            type(self._source)
            is getattr(sys.modules["__main__"], "SourceRuntimeBinding", None)
        )
        require(type(self._source.files) is NativeFiles)
        require(type(self._source.runtime) is SnapshotRuntime)
        require(type(self._source.postgresql) is PostgreSQLRuntime)
        require(self._source.runtime._files is self._source.files)
        require(self._source.postgresql._files is self._source.files)
        require(
            type(self._area) is BoundArea
            and type(self._area.owner) is SnapshotWorkspace
        )
        require(
            self._area.owner._files is self._source.files
            and self._area.owner._runtime is self._source.runtime
        )
        require(
            self._area.owner._manifest_sha == self._inputs.workspace_manifest_sha256
        )
        require(
            type(self._session) is SnapshotSession
            and self._session._files is self._source.files
        )
        require(
            type(self._session._cli) is OwnedAzureCLI
            and self._session._cli._runtime is self._source.runtime
        )
        configuration = self._session._cli._copy
        require(
            type(configuration) is BoundConfiguration
            and configuration.area is self._area
        )
        require(
            type(configuration.owner) is ConfiguredSource
            and configuration.owner._files is self._source.files
        )
        require(
            type(self._console) is SnapshotConsole
            and self._console._inputs == self._inputs
        )
        require(self._console._confirmed is True)

    def _runtime_checks(self) -> None:
        self._types()
        self._source.revalidate()

    def _local_checks(self) -> None:
        self._runtime_checks()
        self._artifact_revalidate()
        require(datetime.now(UTC) < self._inputs.plan_expires_at)

    def _local_until(self, deadline: float, *, cleanup: bool = False) -> None:
        with capture_deadline(deadline):
            if cleanup:
                self._runtime_checks()
            else:
                self._local_checks()

    def _same_inputs(self, inputs: CaptureInputs) -> None:
        require(type(inputs) is CaptureInputs and inputs == self._inputs)

    def _same_identity(self, evidence: dict[str, object]) -> None:
        require({key: evidence.get(key) for key in IDENTITY_KEYS} == self._identity)
        require(
            evidence.get("configured_home_account_linked_to_exact_arm_token") is True
        )

    def _remaining(self, deadline: float, maximum: int) -> float:
        remaining = min(maximum, deadline - time.monotonic())
        require(remaining > 0)
        return remaining

    def _identity_until(self, deadline: float, minimum_life: int) -> None:
        current = self._session.verify(
            deadline=deadline, minimum_remaining_seconds=minimum_life
        )
        self._same_identity(current)
        observed = self._azure.observe_server(self._remaining(deadline, 30))
        require(observed.get("target_matches") is True)
        evidence = observed["identity_evidence"]
        require(type(evidence) is dict)
        evidence = cast(dict[str, object], evidence)
        self._same_identity(evidence)
        require(evidence.get("authenticated_same_token_arm_read") is True)
        require(evidence.get("authenticated_same_token_read_required") is False)

    def monotonic(self) -> float:
        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(UTC)

    def revalidate_human_gate(self, inputs: CaptureInputs, timeout: float) -> None:
        self._same_inputs(inputs)
        deadline = deadline_for(timeout, 30)
        self._local_until(deadline)
        inputs.validate(self.utcnow())

    def revalidate_runtime_identity(
        self, inputs: CaptureInputs, timeout: float
    ) -> None:
        self._same_inputs(inputs)
        deadline = deadline_for(timeout, 30)
        self._local_until(deadline)
        self._identity_until(deadline, 240)
        self._local_until(deadline)

    def current_public_ipv4(self, timeout: float) -> str:
        self._types()
        require(
            type(self._ip) is SnapshotPublicIP and self._ip._files is self._source.files
        )
        return self._ip.current_ipv4(timeout)

    def list_rules(self, timeout: float) -> tuple[Rule, ...]:
        deadline = deadline_for(timeout, 30)
        cleanup = self._closure is not None
        self._local_until(deadline, cleanup=cleanup)
        result = self._azure.list_rules(self._remaining(deadline, 30))
        require(type(self._azure._identity) is dict)
        self._same_identity(cast(dict[str, object], self._azure._identity))
        self._local_until(deadline, cleanup=cleanup)
        indexed = rules_by_name(result)
        if self._window is None:
            require(self._inputs.rule.name.casefold() not in indexed)
            require(self._baseline is None or self._baseline == indexed)
            self._baseline = indexed
        if cleanup:
            require(self._closure == HumanClosure(True, True))
            require(self._baseline is not None and indexed == self._baseline)
            require(self._inputs.rule.name.casefold() not in indexed)
            self._closure_verified = True
        return result

    def verify_empty_encrypted_payload(
        self, inputs: CaptureInputs, timeout: float
    ) -> None:
        self._same_inputs(inputs)
        require(not self._capture_attempted)
        deadline = deadline_for(timeout, 30)
        with capture_deadline(deadline):
            self._local_checks()
            self._area.owner.revalidate(self._area, deadline=deadline)
            require(not os.listdir(self._area.payload_fd))
            self._local_checks()

    def present_manual_window_instructions(
        self, inputs: CaptureInputs, window_closes_at: datetime, timeout: float
    ) -> None:
        self._same_inputs(inputs)
        require(self._window is None and self._baseline is not None)
        require(type(window_closes_at) is datetime and window_closes_at.tzinfo is UTC)
        remaining = (window_closes_at - self.utcnow()).total_seconds()
        require(0 < remaining <= 1800)
        deadline = deadline_for(timeout, 30)
        self._local_until(deadline)
        # Cover the WHOLE remaining action+cleanup, not the per-read240s floor.
        self._identity_until(deadline, math.ceil(remaining) + 30)
        self._local_until(deadline)
        inputs.validate(self.utcnow())
        self._window = window_closes_at
        self._console.present_manual_window_instructions(
            inputs, window_closes_at, self._remaining(deadline, 30)
        )

    def await_human_rule_creation(self, rule: Rule, timeout: float) -> bool:
        require(
            type(rule) is Rule
            and rule == self._inputs.rule
            and self._window is not None
        )
        self._types()
        return self._console.await_human_rule_creation(rule, timeout)

    def capture_to_exclusive_encrypted_descriptor(
        self, inputs: CaptureInputs, timeout: float, max_bytes: int
    ) -> Archive:
        self._same_inputs(inputs)
        require(not self._capture_attempted and self._window is not None)
        require(self._console._creation_answered is True)
        require(type(max_bytes) is int and 0 < max_bytes <= 1024**3)
        inputs.validate(self.utcnow())
        self._capture_attempted = True
        deadline = deadline_for(timeout, 1200)
        self._local_until(deadline)
        credentials, application_sha = self._azure.read_bound_database_credentials(
            IMAGE, self._remaining(deadline, 120)
        )
        try:
            require(application_sha == self._application_sha)
            self._local_until(deadline)
            require(self.utcnow() < inputs.handoff_expires_at)
            payload = PayloadBinding(
                self._area.payload_fd, self._area.mount_device, self._area.payload_inode
            )
            self._children = BoundedChildren(deadline, self._local_checks)

            def encrypted_check() -> None:
                self._local_checks()
                self._area.owner.revalidate(self._area, deadline=deadline)
                self._local_checks()

            result = capture_with_verified_payload(
                credentials, payload, self._children, encrypted_check, max_bytes
            )
            require(type(result) is DumpResult)
            archive = Archive(
                result.size, result.sha256, result.source_revision, True, True
            )
            archive.validate()
            self._archive = archive
            return archive
        finally:
            del credentials

    def cancel_capture_and_reap(self, timeout: float) -> None:
        # Do not require fresh artifacts/identity/plan/console for cancellation.
        deadline = deadline_for(timeout, 15)
        if self._children is not None:
            require(type(self._children) is BoundedChildren)
            with capture_deadline(deadline):
                self._children.cancel_and_reap()

    def await_human_rule_removal(self, rule: Rule, timeout: float) -> HumanClosure:
        # This fallback must survive expiration, failed source checks, partial
        # creation instructions and uncertain child cancellation.
        require(type(self._console) is SnapshotConsole)
        require(type(rule) is Rule and rule == self._inputs.rule)
        closure = self._console.await_human_rule_removal(rule, timeout)
        require(type(closure) is HumanClosure)
        self._closure = closure
        return closure

    def _metadata(self, elapsed: float) -> dict[str, object]:
        archive, inputs = self._archive, self._inputs
        require(archive is not None)
        if archive is None:
            raise SnapshotOperationsRefused("snapshot_operations_refused")
        return {
            "schema_version": 2,
            "status": "captured_human_closure_observed",
            "source_commit": inputs.source_commit,
            "plan_sha256": inputs.plan_sha256,
            "identity_evidence_sha256": inputs.identity_evidence_sha256,
            "workspace_manifest_sha256": inputs.workspace_manifest_sha256,
            "manual_window_approval_sha256": inputs.manual_window_approval_sha256,
            "firewall_management": "human_only",
            "operator_reports_all_mutations_terminal": True,
            "operator_reports_rule_removal_completed": True,
            "atomic_rule_ownership_proved": False,
            "native_firewall_expiry_proved": False,
            "crash_proof_cleanup_proved": False,
            "concurrent_edit_risk_accepted_in_reviewed_plan": True,
            "archive_sha256": archive.sha256,
            "archive_bytes": archive.size,
            "source_revision": archive.source_revision,
            "rule_name_sha256": hashlib.sha256(inputs.rule.name.encode()).hexdigest(),
            "new_rule_absent": True,
            "preexisting_rules_unchanged": True,
            "elapsed_seconds": elapsed,
            "restore_or_migration_accepted": False,
            "deployment_authorized": False,
            "not_a_receipt_or_reusable_authorization": True,
        }

    def publish_metadata(self, metadata: dict[str, object], timeout: float) -> None:
        require(self._closure_verified and self._closure == HumanClosure(True, True))
        require(
            self._archive is not None
            and not self._published
            and self._capture_attempted
        )
        require(type(metadata) is dict)
        elapsed = metadata.get("elapsed_seconds")
        require(type(elapsed) in {float, int})
        elapsed = cast(float, elapsed)
        require(math.isfinite(elapsed) and 0 <= elapsed < 1800)
        require(canonical(metadata) == canonical(self._metadata(elapsed)))
        raw = canonical(metadata) + b"\n"
        require(0 < len(raw) <= 16384)
        self._published = True
        deadline = deadline_for(timeout, 15)
        with capture_deadline(deadline):
            self._local_checks()
            self._area.owner.revalidate(self._area, deadline=deadline)
            with private_new_file(self._area.mount_fd, METADATA_NAME) as fd:
                before = os.fstat(fd)
                require(
                    before.st_dev == self._area.mount_device and before.st_nlink == 1
                )
                view = memoryview(raw)
                while view:
                    require(time.monotonic() < deadline)
                    count = os.write(fd, view)
                    require(count > 0)
                    view = view[count:]
                os.fchmod(fd, 0o400)
                os.fsync(fd)
                current = os.fstat(fd)
                self._source.files.acl_safe(fd)
                require(
                    current.st_uid == os.geteuid() and stat.S_ISREG(current.st_mode)
                )
                require(
                    current.st_dev == before.st_dev and current.st_ino == before.st_ino
                )
                require(
                    current.st_size == len(raw)
                    and current.st_nlink == 1
                    and stat.S_IMODE(current.st_mode) == 0o400
                )
                require(
                    identity(current)
                    == identity(
                        os.stat(
                            METADATA_NAME,
                            dir_fd=self._area.mount_fd,
                            follow_symlinks=False,
                        )
                    )
                )
            self._area.owner.revalidate(self._area, deadline=deadline)
            self._local_checks()


if __name__ == "__main__":
    raise SystemExit("snapshot_operations_requires_reviewed_complete_human_launcher")
