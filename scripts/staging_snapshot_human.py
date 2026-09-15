"""HUMAN-ONLY snapshot composition; agents may invoke invented fixture tests only.

The independently hash-checked bootstrap supplies its sealed exact source/runtime
binding. Actual source/plan/handoff review hashes and current foreground Terminal
presence remain mandatory. No firewall mutation, migration, deployment, artifact
minting, claim creation, retry or automatic cleanup is implemented here.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from staging_snapshot_artifacts import (
    PreparationArtifacts,
    ReviewedArtifacts,
    exact,
    record,
    sha,
)
from staging_snapshot_azure import SnapshotAzure
from staging_snapshot_capture import (
    SAFE_REFUSAL_CODES,
    CaptureRefused,
    execute_after_human_gate,
    rules_by_name,
)
from staging_snapshot_cli import OwnedAzureCLI
from staging_snapshot_configuration import (
    ConfiguredSource,
    private_new_file,
    private_umask,
)
from staging_snapshot_console import PreparationConsole, SnapshotConsole
from staging_snapshot_native_files import identity
from staging_snapshot_operations import (
    IDENTITY_KEYS,
    IMAGE,
    SnapshotOperations,
    canonical,
)
from staging_snapshot_pg_io import capture_deadline, require_capture_process
from staging_snapshot_public_ip import SnapshotPublicIP, canonical_ipv4
from staging_snapshot_session import SnapshotSession, expected_identity
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


def preparation_observation(
    artifacts, observed, application, rules, public_ip
) -> bytes:
    """Only a strict minimized projection, never a provider body or credential."""
    require(observed.get("target_matches") is True)
    evidence = record(
        observed.get("identity_evidence"),
        "configured_home_account_linked_to_exact_arm_token configured_username_sha256 "
        "current_token_oid_sha256 home_account_id_sha256 exact_arm_token_sha256 "
        "token_expires_epoch observed_at_epoch jwt_signature_independently_verified "
        "authenticated_same_token_read_required historical_apply_oid_attested "
        "human_apply_authorized not_a_plan_handoff_or_receipt authenticated_same_token_arm_read "
        "arm_read_response_epoch arm_read_body_sha256",
    )
    exact(
        evidence,
        {
            "configured_username_sha256": expected_identity().configured_username_sha256,
            "configured_home_account_linked_to_exact_arm_token": True,
            "jwt_signature_independently_verified": False,
            "authenticated_same_token_read_required": False,
            "historical_apply_oid_attested": False,
            "human_apply_authorized": False,
            "not_a_plan_handoff_or_receipt": True,
            "authenticated_same_token_arm_read": True,
        },
    )
    for key in (*IDENTITY_KEYS, "arm_read_body_sha256"):
        sha(evidence[key])
    require(evidence["arm_read_body_sha256"] == observed.get("server_body_sha256"))
    require(evidence["exact_arm_token_sha256"] == observed.get("exact_token_sha256"))
    now = datetime.now(UTC)
    for key in ("token_expires_epoch", "observed_at_epoch", "arm_read_response_epoch"):
        require(type(evidence[key]) is int and evidence[key] > 0)
    require(now.timestamp() - 120 <= evidence["observed_at_epoch"] <= now.timestamp())
    require(
        abs(evidence["observed_at_epoch"] - evidence["arm_read_response_epoch"]) <= 120
    )
    require(evidence["arm_read_response_epoch"] <= now.timestamp() + 5)
    # Preserve room for the subsequent capture loader + preparation + session
    # floor. Review/interaction latency needs ADDITIONAL lifetime, never renewal.
    require(evidence["token_expires_epoch"] > now.timestamp() + 2430)
    require(type(public_ip) is str and canonical_ipv4(public_ip.encode()) == public_ip)
    baseline = rules_by_name(rules)
    p = artifacts.preparation
    return canonical(
        {
            "schema_version": 1,
            "source_commit": artifacts.source_commit,
            "source_bundle_sha256": artifacts.source_bundle_sha256,
            "operator_uid": p["operator_uid"],
            "observed_at": now.isoformat().replace("+00:00", "Z"),
            "identity": evidence,
            **{
                key: p[key]
                for key in (
                    "workspace_manifest_sha256",
                    "hdiutil_sha256",
                    "diskutil_sha256",
                )
            },
            "application_binding_sha256": sha(application),
            "image": IMAGE,
            "public_ipv4": public_ip,
            "preexisting_rules": [
                {"name": rule.name, "start": rule.start, "end": rule.end}
                for _, rule in sorted(baseline.items())
            ],
            "preparation_only": True,
            "database_credentials_read": False,
            "firewall_mutated": False,
        }
    )


def publish_preparation(artifacts: PreparationArtifacts, raw: bytes) -> dict[str, Any]:
    """Exclusive public output in a DIFFERENT directory from retained inputs.

    Caller owns an active preparation timer. Preserve partial output on failure;
    no overwrite, deletion, retry or success on a failed close/revalidation.
    """
    require_capture_process()
    require(type(artifacts) is PreparationArtifacts and type(raw) is bytes)
    require(0 < len(raw) <= 16384)
    artifacts.revalidate()
    artifacts.require_start()
    private_umask()
    files = artifacts._source.files
    path = artifacts.observation_root / "preparation-observation.json"
    with files.protected_parent(path) as parent:
        before = os.fstat(parent)
        require(
            stat.S_ISDIR(before.st_mode)
            and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o700
            and not os.listdir(parent)
        )
        files.acl_safe(parent)
        with private_new_file(parent, path.name) as descriptor:
            files.acl_safe(descriptor)
            view = memoryview(raw)
            while view:
                count = os.write(descriptor, view)
                require(count > 0)
                view = view[count:]
            os.fchmod(descriptor, 0o400)
            os.fsync(descriptor)
            current = os.fstat(descriptor)
            require(
                stat.S_ISREG(current.st_mode)
                and current.st_uid == os.geteuid()
                and stat.S_IMODE(current.st_mode) == 0o400
                and current.st_nlink == 1
                and current.st_size == len(raw)
                and identity(current)
                == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
            )
            files.acl_safe(descriptor)
        os.fsync(parent)
    retained = files.read(path, mode=0o400, maximum=16384)
    require(retained.content == raw and retained.file_identity == identity(current))
    artifacts.revalidate()
    artifacts.require_start()
    return {
        "status": "read_only_preparation_observed",
        "observation_sha256": hashlib.sha256(raw).hexdigest(),
        "observation_bytes": len(raw),
        "execution_authorized": False,
        "deployment_authorized": False,
    }


def run_human_preparation(
    source: SourceRuntimeBinding,
    packet: Path,
    expected_preparation_sha256: str,
    expected_source_review_sha256: str,
) -> dict[str, Any]:
    """HUMAN-ONLY pre-plan observation. Tests replace ALL human/credential I/O.

    One preparation IP check + the capture's two checks = approved three-check
    workflow. This never calls capture, listSecrets, PostgreSQL or a mutation.
    The final capture still owes exact independent plan AND handoff review.
    """
    require(not _attempted)
    _attempted.append(True)
    console = None
    # Read-only preparation may spend up to ten minutes across all phases.
    # Individual operation limits and the separate capture clock are unchanged.
    deadline = time.monotonic() + 600

    def phase(maximum):
        now = time.monotonic()
        result = min(deadline, now + maximum)
        require(result > now)
        return result

    def remaining(maximum):
        result = phase(maximum) - time.monotonic()
        require(result > 0)
        return result

    try:
        require_capture_process()
        require(
            type(source)
            is getattr(sys.modules["__main__"], "SourceRuntimeBinding", None)
        )
        with capture_deadline(phase(30)):
            source.revalidate()
            artifacts = PreparationArtifacts(
                source,
                packet,
                expected_preparation_sha256,
                expected_source_review_sha256,
            )

        def check():
            # Used within caller-owned timers, including the IP adapter's timer.
            artifacts.revalidate()
            artifacts.require_start()

        console = PreparationConsole()
        console.confirm_preparation(artifacts.preparation_sha256, remaining(120))
        with capture_deadline(phase(30)):
            check()
        p = artifacts.preparation
        workspace = SnapshotWorkspace(
            source.files,
            source.runtime,
            artifacts.workspace_root,
            p["workspace_manifest_sha256"],
            p["workspace_source_sha256"],
            p["hdiutil_sha256"],
            p["diskutil_sha256"],
        )
        with workspace.open_area(deadline=phase(30)) as area:
            configured = ConfiguredSource(source.files, deadline=phase(30))
            with configured.private_copy(area, deadline=phase(60)) as copied:
                session = SnapshotSession(
                    OwnedAzureCLI(source.runtime, copied),
                    source.files,
                    deadline=phase(120),
                )
                azure = SnapshotAzure(session)
                application = azure.observe_application_binding(IMAGE, remaining(60))
                rules = azure.list_rules(remaining(30))
                public_ip = SnapshotPublicIP(source.files, check).current_ipv4(
                    remaining(15)
                )
                with capture_deadline(recheck_deadline := phase(30)):
                    check()
                    workspace.revalidate(area, deadline=recheck_deadline)
                    configured.revalidate()
                # Final same-token ARM server request gives fresh identity after
                # the expensive workspace/CLI preparation, not a guessed epoch.
                observed = azure.observe_server(remaining(30))
                with capture_deadline(phase(30)):
                    check()
                    configured.revalidate()
                    raw = preparation_observation(
                        artifacts, observed, application, rules, public_ip
                    )
        console.close()
        console = None
        with capture_deadline(phase(30)):
            check()
            return publish_preparation(artifacts, raw)
    except BaseException:
        raise SnapshotHumanRefused("snapshot_preparation_refused") from None
    finally:
        if console is not None:
            try:
                console.close()
            except BaseException:
                raise SnapshotHumanRefused("snapshot_preparation_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_human_requires_exact_reviewed_bootstrap")
