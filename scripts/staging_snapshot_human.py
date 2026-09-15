"""HUMAN-ONLY snapshot composition; agents may invoke invented fixture tests only.

The independently hash-checked bootstrap supplies its sealed exact source/runtime
binding. Actual source/plan/handoff review hashes and current foreground Terminal
presence remain mandatory. No firewall mutation, migration, deployment, artifact
minting, claim creation, retry or automatic cleanup is implemented here.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from staging_snapshot_artifacts import ReviewedArtifacts, exact
from staging_snapshot_azure import SnapshotAzure
from staging_snapshot_capture import (
    SAFE_REFUSAL_CODES,
    CaptureRefused,
    execute_after_human_gate,
)
from staging_snapshot_cli import OwnedAzureCLI
from staging_snapshot_configuration import ConfiguredSource
from staging_snapshot_console import SnapshotConsole
from staging_snapshot_operations import IDENTITY_KEYS, SnapshotOperations
from staging_snapshot_pg_io import capture_deadline, require_capture_process
from staging_snapshot_session import SnapshotSession
from staging_snapshot_workspace import SnapshotWorkspace

if TYPE_CHECKING:
    from staging_snapshot_bootstrap import SourceRuntimeBinding

REFUSAL_CODES = SAFE_REFUSAL_CODES | {
    "snapshot_preparation_refused",
    "snapshot_local_close_failed_no_acceptance",
    "snapshot_capture_failed_human_action_required",
}
# Mutable container preserves the sealed module's object binding. No code path
# clears this marker; it is process-local one-attempt state, NOT a durable claim.
_attempted: list[bool] = []


class SnapshotHumanRefused(RuntimeError):
    def __init__(self, code: object):
        super().__init__(
            code
            if type(code) is str and code in REFUSAL_CODES
            else "snapshot_preparation_refused"
        )


def require(value: object) -> None:
    if not value:
        raise SnapshotHumanRefused("snapshot_preparation_refused")


def capture_error(error: BaseException) -> str:
    if (
        type(error) is CaptureRefused
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in SAFE_REFUSAL_CODES
    ):
        return error.args[0]
    return "snapshot_capture_failed_human_action_required"


def run_human_snapshot(
    source: SourceRuntimeBinding,
    packet: Path,
    expected_handoff_sha256: str,
    expected_review_summary_sha256: str,
) -> dict[str, Any]:
    """One foreground human attempt. Never call against real inputs from an agent.

    All preparation shares a 240-second deadline. Callees own sequential native
    alarms; no nested or renewed per-operation timers. The capture core separately
    owns its existing 30-minute action/closure clock. Artifact expiry still gates
    both confirmation and capture. No new handoff can be produced by this function.
    """
    require(not _attempted)
    _attempted.append(True)
    console = None
    result = None
    failure: str | None = None
    capture_entered = False
    preparation_deadline = time.monotonic() + 240

    def phase(maximum: float) -> float:
        now = time.monotonic()
        deadline = min(preparation_deadline, now + maximum)
        require(deadline > now)
        return deadline

    def remaining(maximum: float) -> float:
        value = phase(maximum) - time.monotonic()
        require(value > 0)
        return value

    try:
        require_capture_process()
        require(
            type(source)
            is getattr(sys.modules["__main__"], "SourceRuntimeBinding", None)
        )
        with capture_deadline(phase(30)):
            source.revalidate()
            artifacts = ReviewedArtifacts(
                source, packet, expected_handoff_sha256, expected_review_summary_sha256
            )

        def artifact_start_check() -> None:
            with capture_deadline(phase(30)):
                artifacts.revalidate()
                artifacts.require_start()

        # Retain/verify the real foreground Terminal BEFORE any account/cache
        # access. The two actual confirmations follow identity preparation and
        # precede all rule-creation instructions and capture.
        console = SnapshotConsole(artifacts.inputs)
        artifact_start_check()
        plan = artifacts.plan
        workspace = SnapshotWorkspace(
            source.files,
            source.runtime,
            artifacts.workspace_root,
            artifacts.inputs.workspace_manifest_sha256,
            plan["workspace_source_sha256"],
            plan["hdiutil_sha256"],
            plan["diskutil_sha256"],
        )
        with workspace.open_area(deadline=phase(30)) as area:
            artifact_start_check()
            configured = ConfiguredSource(source.files, deadline=phase(30))
            with configured.private_copy(area, deadline=phase(60)) as copied:
                artifact_start_check()
                cli = OwnedAzureCLI(source.runtime, copied)
                session = SnapshotSession(cli, source.files, deadline=phase(120))
                observed = SnapshotAzure(session).observe_server(remaining(30))
                require(observed.get("target_matches") is True)
                evidence = observed.get("identity_evidence")
                require(type(evidence) is dict)
                evidence = cast(dict[str, Any], evidence)
                exact(
                    evidence,
                    {
                        key: artifacts.identity[key]
                        for key in (
                            *IDENTITY_KEYS,
                            "token_expires_epoch",
                            "configured_home_account_linked_to_exact_arm_token",
                            "authenticated_same_token_arm_read",
                            "authenticated_same_token_read_required",
                            "human_apply_authorized",
                        )
                    },
                )
                require(
                    observed.get("server_body_sha256")
                    == artifacts.identity["arm_read_body_sha256"]
                )
                artifact_start_check()
                console.confirm_start(remaining(120))
                artifact_start_check()
                operations = SnapshotOperations(
                    artifacts.inputs,
                    source,
                    area,
                    session,
                    console,
                    artifacts.identity,
                    plan["application_binding_sha256"],
                    tuple(artifacts.baseline.values()),
                    artifacts.revalidate,
                )
                # Catch INSIDE both descriptor contexts. Their fixed-code
                # cleanup wrappers must not erase a required-human-removal code.
                capture_entered = True
                try:
                    result = execute_after_human_gate(artifacts.inputs, operations)
                except BaseException as error:
                    failure = capture_error(error)
    except BaseException:
        if failure is None:
            failure = (
                "snapshot_local_close_failed_no_acceptance"
                if capture_entered
                else "snapshot_preparation_refused"
            )
    finally:
        if console is not None:
            try:
                console.close()
            except BaseException:
                if failure is None:
                    failure = "snapshot_local_close_failed_no_acceptance"
    if failure is not None:
        raise SnapshotHumanRefused(failure) from None
    require(type(result) is dict)
    return cast(dict[str, Any], result)


if __name__ == "__main__":
    raise SystemExit("snapshot_human_requires_exact_reviewed_bootstrap")
