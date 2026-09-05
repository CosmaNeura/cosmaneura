#!/usr/bin/env python3
"""Create or revalidate the one-time non-mutating staging recovery plan."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REHEARSAL_ROOT = REPOSITORY_ROOT / "infra-staging-rehearsal"
VALIDATOR_PATH = Path(__file__).resolve()

EXPECTED_LINEAGE = "76b6712e-5781-adbd-9fb1-cf66db4f4e28"
EXPECTED_POST_UNTAINT_SERIAL = 8
EXPECTED_FORMAT_VERSION = "1.2"
EXPECTED_TOFU_VERSION = "1.12.5"
EXPECTED_SUBSCRIPTION_ID = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
EXPECTED_TENANT_ID = "d51754d0-bba5-4810-820e-afac542844d6"
EXPECTED_ORIGIN_URL = "git@github.com:CosmaNeura/cosmaneura.git"
SYSTEM_GIT = Path("/usr/bin/git")
SYSTEM_SSH = Path("/usr/bin/ssh")
GITHUB_HOST_KEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
MAXIMUM_ARTIFACT_BYTES = 64 * 1024 * 1024
MAXIMUM_PLAN_JSON_BYTES = 128 * 1024 * 1024
MAXIMUM_PLAN_ARCHIVE_BYTES = 256 * 1024 * 1024
MAXIMUM_PLAN_AGE = timedelta(hours=4)
MAXIMUM_CLOCK_SKEW = timedelta(minutes=5)

PLAN_VARIABLE_ARGUMENTS = (
    "-var=deploy_staging_restore_control_plane=true",
    "-var=target_cidr_inventory_revalidated=true",
    "-var=control_cidr_inventory_revalidated=true",
)

EXPECTED_EXISTING_ADDRESSES: frozenset[str] = frozenset(
    """
azurerm_federated_identity_credential.dispatcher["cleanup"]
azurerm_federated_identity_credential.dispatcher["provision"]
azurerm_policy_definition.deny_postgres_firewall_rules[0]
azurerm_private_dns_zone.marker_blob[0]
azurerm_private_dns_zone_virtual_network_link.marker_blob[0]
azurerm_resource_group.control[0]
azurerm_resource_group.target[0]
azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules[0]
azurerm_role_assignment.control_reader["cleanup"]
azurerm_role_assignment.control_reader["provision"]
azurerm_role_assignment.source_reader[0]
azurerm_role_assignment.target_cleanup[0]
azurerm_role_assignment.target_provisioner[0]
azurerm_role_definition.control_reader[0]
azurerm_role_definition.marker_reader[0]
azurerm_role_definition.source_reader[0]
azurerm_role_definition.target_cleanup[0]
azurerm_role_definition.target_provisioner[0]
azurerm_storage_account.marker[0]
azurerm_subnet.marker_private_endpoint[0]
azurerm_subnet.private_executor_reserved[0]
azurerm_user_assigned_identity.dispatcher["cleanup"]
azurerm_user_assigned_identity.dispatcher["provision"]
azurerm_user_assigned_identity.job["cleanup"]
azurerm_user_assigned_identity.job["provision"]
azurerm_virtual_network.control[0]
terraform_data.activation_guard[0]
terraform_data.private_executor_topology_blocker[0]
""".strip().splitlines()
)

EXPECTED_CREATE_ADDRESSES: frozenset[str] = frozenset(
    """
azapi_resource.marker_container["restore-cleanups-v3"]
azapi_resource.marker_container["restore-cleanups-v4"]
azapi_resource.marker_container["restore-evidence-v1"]
azapi_resource.marker_container["restore-intents-v3"]
azapi_resource.marker_container["restore-intents-v4"]
azapi_resource.marker_container["restore-submissions-v3"]
azapi_resource.marker_container["restore-submissions-v4"]
azurerm_private_endpoint.marker[0]
azurerm_role_assignment.marker_provision_cleanup[0]
azurerm_role_assignment.marker_read["cleanup_intent"]
azurerm_role_assignment.marker_read["cleanup_submission"]
""".strip().splitlines()
)

EXPECTED_VARIABLES: Mapping[str, Any] = {
    "control_cidr_inventory_revalidated": True,
    "deploy_staging_restore_control_plane": True,
    "enable_marker_create_assignments": False,
    "enable_marker_legal_holds": False,
    "enable_marker_readonly_lock": False,
    "enable_private_executor_network_foundation": False,
    "irreversible_marker_controls_approved": False,
    "lock_marker_immutability_policies": False,
    "marker_create_authorization_proven": False,
    "marker_create_data_actions": [],
    "marker_legal_hold_readback_proven": False,
    "marker_storage_account_name": "cosmaneurarehearsalctl",
    "private_executor_network_plan_approved": False,
    "private_marker_network_posture_proven": False,
    "subscription_id": EXPECTED_SUBSCRIPTION_ID,
    "tags": {
        "data-classification": "non-phi-control-metadata",
        "environment": "staging",
        "managed-by": "opentofu",
        "purpose": "staging-restore-rehearsal-control",
    },
    "target_cidr_inventory_revalidated": True,
    "tenant_id": EXPECTED_TENANT_ID,
}
EXPECTED_BACKEND_CONFIG: Mapping[str, Any] = {
    "resource_group_name": "cosmaneura-tfstate",
    "storage_account_name": "cosmaneuratfstate",
    "container_name": "cosmaneura-tfstate",
    "key": "staging-rehearsal-control-plane.tfstate",
    "subscription_id": EXPECTED_SUBSCRIPTION_ID,
    "environment": "public",
    "use_azuread_auth": True,
    "use_cli": True,
}


class RecoveryPlanError(ValueError):
    """Raised when an artifact cannot authorize the recovery plan."""


@dataclass(frozen=True)
class SourceSnapshot:
    commit: str
    terraform_files: Mapping[str, bytes]
    lockfile: bytes
    tree_sha256: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_file_set_sha256(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        encoded_name = name.encode("utf-8")
        value = files[name]
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _reject_symlink_components(
    path: Path, *, label: str, allow_missing_leaf: bool = False
) -> None:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise RecoveryPlanError(f"{label} path must be absolute and normalized")
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current /= part
        is_leaf = index == len(parts) - 1
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as exc:
            if allow_missing_leaf and is_leaf:
                return
            raise RecoveryPlanError(f"{label} path is unavailable") from exc
        except OSError as exc:
            raise RecoveryPlanError(f"{label} path is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RecoveryPlanError(f"{label} path contains a symbolic link")


def _open_protected_directory(path: Path, *, label: str) -> int:
    _reject_symlink_components(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise RecoveryPlanError(f"{label} is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise RecoveryPlanError(f"{label} is not a directory")
    if metadata.st_uid != os.geteuid():
        os.close(descriptor)
        raise RecoveryPlanError(f"{label} is not owned by the current operator")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o077 or mode & 0o700 != 0o700:
        os.close(descriptor)
        raise RecoveryPlanError(f"{label} must have mode 0700")
    return descriptor


def _require_protected_directory(path: Path, *, label: str) -> None:
    os.close(_open_protected_directory(path, label=label))


def _create_protected_directory(path: Path, *, label: str) -> None:
    _reject_symlink_components(path, label=label, allow_missing_leaf=True)
    parent = _open_protected_directory(path.parent, label=f"{label} parent")
    try:
        os.mkdir(path.name, 0o700, dir_fd=parent)
    except OSError as exc:
        raise RecoveryPlanError(f"{label} must not already exist") from exc
    finally:
        os.close(parent)
    _require_protected_directory(path, label=label)


def _executable_sha256(path: Path, *, label: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o111:
            raise RecoveryPlanError(f"{label} is not an executable regular file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RecoveryPlanError(f"{label} changed while it was hashed")
        return digest.hexdigest()
    except OSError as exc:
        raise RecoveryPlanError(f"{label} could not be hashed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _provider_tree_sha256(data_dir: Path) -> str:
    root = data_dir / "providers"
    paths = sorted(root.rglob("terraform-provider-*"))
    if len(paths) != 2 or any(path.is_symlink() for path in paths):
        raise RecoveryPlanError("fresh provider executable set is not exact")
    return _canonical_file_set_sha256(
        {
            str(path.relative_to(root)): bytes.fromhex(
                _executable_sha256(path, label="provider executable")
            )
            for path in paths
        }
    )


def _read_protected_file(
    path: Path, *, label: str, required_mode: int | None = None
) -> bytes:
    _reject_symlink_components(path.parent, label=f"{label} directory")
    parent_descriptor = _open_protected_directory(
        path.parent, label=f"{label} directory"
    )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise RecoveryPlanError(f"{label} is unavailable") from exc
    finally:
        os.close(parent_descriptor)
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if not stat.S_ISREG(before.st_mode):
            raise RecoveryPlanError(f"{label} must be a regular file")
        if before.st_uid != os.geteuid():
            raise RecoveryPlanError(f"{label} is not owned by the current operator")
        if before.st_nlink != 1:
            raise RecoveryPlanError(f"{label} must have exactly one hard link")
        if mode not in {0o400, 0o600} or (
            required_mode is not None and mode != required_mode
        ):
            expected = f"{required_mode:04o}" if required_mode else "0400 or 0600"
            raise RecoveryPlanError(f"{label} must have mode {expected}")
        if before.st_size <= 0 or before.st_size > MAXIMUM_ARTIFACT_BYTES:
            raise RecoveryPlanError(f"{label} size is outside the accepted range")

        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(value) != before.st_size or identity_before != identity_after:
            raise RecoveryPlanError(f"{label} changed while it was being read")
        return value
    except OSError as exc:
        raise RecoveryPlanError(f"{label} could not be read") from exc
    finally:
        os.close(descriptor)


def _write_protected_file(path: Path, value: bytes, *, label: str) -> None:
    _reject_symlink_components(path, label=label, allow_missing_leaf=True)
    parent_descriptor = _open_protected_directory(
        path.parent, label=f"{label} directory"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path.name, flags, 0o400, dir_fd=parent_descriptor)
        created = True
        offset = 0
        while offset < len(value):
            offset += os.write(descriptor, value[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
    except OSError as exc:
        if created:
            try:
                os.unlink(path.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise RecoveryPlanError(f"{label} could not be written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _load_json_bytes(value: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryPlanError(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise RecoveryPlanError(f"{label} must contain a JSON object")
    return decoded


def _normalized_environment(*, data_dir: Path | None = None) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("TF_", "TOFU_", "OPENTOFU_", "GIT_", "ARM_", "AZURE_"))
    }
    environment["CHECKPOINT_DISABLE"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["TF_CLI_CONFIG_FILE"] = os.devnull
    if data_dir is not None:
        environment["TF_DATA_DIR"] = str(data_dir)
    environment["TF_IN_AUTOMATION"] = "1"
    environment["ARM_SUBSCRIPTION_ID"] = EXPECTED_SUBSCRIPTION_ID
    environment["ARM_ENVIRONMENT"] = "public"
    environment["ARM_USE_CLI"] = "true"
    environment["ARM_USE_AZUREAD"] = "true"
    return environment


def _run_process(
    arguments: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    pass_fds: Sequence[int] = (),
    max_output_bytes: int = 8 * 1024 * 1024,
    data_dir: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        process = subprocess.Popen(
            list(arguments),
            cwd=cwd,
            env=_normalized_environment(data_dir=data_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            pass_fds=tuple(pass_fds),
            umask=0o077,
            start_new_session=True,
        )
    except OSError as exc:
        raise RecoveryPlanError("required local command did not complete") from exc
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    streams: dict[int, list[bytes]] = {stdout_fd: [], stderr_fd: []}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, stdout_fd)
    selector.register(process.stderr, selectors.EVENT_READ, stderr_fd)
    deadline = time.monotonic() + timeout_seconds
    total = 0
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RecoveryPlanError("required local command timed out")
            for key, _ in selector.select(min(remaining, 0.5)):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > max_output_bytes:
                    raise RecoveryPlanError(
                        "required local command output exceeded limit"
                    )
                streams[key.data].append(chunk)
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired, RecoveryPlanError) as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.stdout.close()
        process.stderr.close()
        process.wait()
        if isinstance(exc, RecoveryPlanError):
            raise
        raise RecoveryPlanError("required local command did not complete") from exc
    finally:
        selector.close()
    stdout = b"".join(streams[stdout_fd])
    stderr = b"".join(streams[stderr_fd])
    process.stdout.close()
    process.stderr.close()
    return subprocess.CompletedProcess(list(arguments), returncode, stdout, stderr)


def _resolve_and_verify_tofu() -> Path:
    executable = shutil.which("tofu")
    if executable is None:
        raise RecoveryPlanError("the pinned OpenTofu executable is unavailable")
    try:
        path = Path(executable).resolve(strict=True)
        metadata = path.stat()
    except OSError as exc:
        raise RecoveryPlanError("the OpenTofu executable is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise RecoveryPlanError("the OpenTofu executable is invalid")
    if (
        metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RecoveryPlanError("the OpenTofu executable has unsafe ownership or mode")

    result = _run_process(
        [str(path), "version", "-json"],
        cwd=REPOSITORY_ROOT,
        timeout_seconds=30,
    )
    if result.returncode != 0 or not result.stdout:
        raise RecoveryPlanError("the OpenTofu version could not be verified")
    version = _load_json_bytes(result.stdout, label="OpenTofu version output")
    if version.get("terraform_version") != EXPECTED_TOFU_VERSION:
        raise RecoveryPlanError("the OpenTofu executable version is not pinned")
    return path


def _show_saved_plan(plan_bytes: bytes, *, tofu: Path, data_dir: Path) -> bytes:
    with tempfile.TemporaryFile(prefix="cosmaneura-recovery-plan-") as plan_file:
        plan_file.write(plan_bytes)
        plan_file.flush()
        plan_file.seek(0)
        descriptor = plan_file.fileno()
        result = _run_process(
            [
                str(tofu),
                f"-chdir={REHEARSAL_ROOT}",
                "show",
                "-json",
                f"/dev/fd/{descriptor}",
            ],
            cwd=REPOSITORY_ROOT,
            timeout_seconds=120,
            pass_fds=(descriptor,),
            max_output_bytes=MAXIMUM_PLAN_JSON_BYTES,
            data_dir=data_dir,
        )
    if result.returncode != 0:
        raise RecoveryPlanError("the saved plan could not be decoded by OpenTofu")
    if not result.stdout or len(result.stdout) > MAXIMUM_PLAN_JSON_BYTES:
        raise RecoveryPlanError("derived plan JSON size is outside the accepted range")
    return result.stdout


def _generate_saved_plan(*, tofu: Path, output_parent: Path, data_dir: Path) -> bytes:
    with tempfile.TemporaryDirectory(
        prefix=".cosmaneura-recovery-plan-", dir=output_parent
    ) as temporary_directory:
        candidate = Path(temporary_directory) / "candidate.tfplan"
        result = _run_process(
            [
                str(tofu),
                f"-chdir={REHEARSAL_ROOT}",
                "plan",
                "-input=false",
                "-lock=true",
                "-lock-timeout=5m",
                "-refresh=true",
                "-detailed-exitcode",
                "-no-color",
                f"-out={candidate}",
                *PLAN_VARIABLE_ARGUMENTS,
            ],
            cwd=REPOSITORY_ROOT,
            timeout_seconds=900,
            data_dir=data_dir,
        )
        if result.returncode != 2:
            raise RecoveryPlanError(
                "the fixed full-refresh plan did not produce the expected change set"
            )
        return _read_protected_file(
            candidate, label="generated saved plan", required_mode=0o600
        )


def _trusted_system_executable(expected: Path, *, label: str) -> Path:
    try:
        path = expected.resolve(strict=True)
        metadata = path.stat()
    except OSError as exc:
        raise RecoveryPlanError(
            f"the system {label} executable is unavailable"
        ) from exc
    if (
        path != expected
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise RecoveryPlanError(f"the system {label} executable is not trusted")
    return path


def _run_git(arguments: Sequence[str], *, repository: bool = True) -> bytes:
    command = [str(_trusted_system_executable(SYSTEM_GIT, label="Git")), "--no-replace-objects"]  # fmt: skip
    if repository:
        command.extend(("-C", str(REPOSITORY_ROOT)))
        result = _run_process([*command, *arguments], cwd=REPOSITORY_ROOT, timeout_seconds=30)  # fmt: skip
    else:
        ssh = _trusted_system_executable(SYSTEM_SSH, label="SSH")
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii") as known_hosts:
            known_hosts.write(f"{GITHUB_HOST_KEY}\n")
            known_hosts.flush()
            os.fchmod(known_hosts.fileno(), 0o400)
            ssh_command = shlex.join([str(ssh), "-F", os.devnull, "-oBatchMode=yes", "-oCanonicalizeHostname=no", "-oHostName=github.com", "-oPort=22", "-oProxyCommand=none", "-oProxyJump=none", "-oStrictHostKeyChecking=yes", f"-oUserKnownHostsFile={known_hosts.name}", "-oGlobalKnownHostsFile=/dev/null", "-oHostKeyAlgorithms=ssh-ed25519"])  # fmt: skip
            result = _run_process([*command, "-c", f"core.sshCommand={ssh_command}", *arguments], cwd=Path("/"), timeout_seconds=30)  # fmt: skip
    if result.returncode != 0:
        raise RecoveryPlanError("the source checkout could not be verified")
    return result.stdout


def _source_snapshot(expected_commit: str) -> SourceSnapshot:
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        raise RecoveryPlanError("expected source commit is not a full lowercase SHA")
    head = _run_git(["rev-parse", "HEAD"]).decode("ascii").strip()
    remote_head = (
        _run_git(["rev-parse", "refs/remotes/origin/master"]).decode("ascii").strip()
    )
    origin = _run_git(["remote", "get-url", "--all", "origin"]).decode("ascii")
    live_remote = _run_git(
        ["ls-remote", EXPECTED_ORIGIN_URL, "refs/heads/master"], repository=False
    ).decode("ascii")
    if (
        head != expected_commit
        or remote_head != expected_commit
        or origin != f"{EXPECTED_ORIGIN_URL}\n"
        or live_remote != f"{expected_commit}\trefs/heads/master\n"
    ):
        raise RecoveryPlanError(
            "source checkout is not the expected protected-master commit"
        )
    status = _run_git(
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            str(REHEARSAL_ROOT.relative_to(REPOSITORY_ROOT)),
            str(VALIDATOR_PATH.relative_to(REPOSITORY_ROOT)),
        ]
    )
    if status:
        raise RecoveryPlanError("recovery source paths are not clean")

    terraform_files: dict[str, bytes] = {}
    for path in sorted(REHEARSAL_ROOT.glob("*.tf")):
        if path.is_symlink() or not path.is_file():
            raise RecoveryPlanError(
                "recovery source contains an invalid Terraform file"
            )
        terraform_files[path.name] = path.read_bytes()
    if not terraform_files:
        raise RecoveryPlanError("recovery Terraform source is missing")
    lock_path = REHEARSAL_ROOT / ".terraform.lock.hcl"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RecoveryPlanError("recovery provider lockfile is missing")
    lockfile = lock_path.read_bytes()
    tree_files = {**terraform_files, ".terraform.lock.hcl": lockfile}
    source_root = REHEARSAL_ROOT.relative_to(REPOSITORY_ROOT)
    expected_names = set(
        _run_git(
            ["ls-tree", "-r", "--name-only", expected_commit, "--", str(source_root)]
        )
        .decode("utf-8")
        .splitlines()
    )
    actual_names = {str(source_root / name) for name in tree_files}
    if actual_names != {
        name for name in expected_names if name.endswith((".tf", ".terraform.lock.hcl"))
    }:
        raise RecoveryPlanError(
            "recovery source file set differs from the expected commit"
        )
    for name, value in tree_files.items():
        if _run_git(["show", f"{expected_commit}:{source_root / name}"]) != value:
            raise RecoveryPlanError(
                "recovery source bytes differ from the expected commit"
            )
    validator_name = str(VALIDATOR_PATH.relative_to(REPOSITORY_ROOT))
    validator_bytes = VALIDATOR_PATH.read_bytes()
    if _run_git(["show", f"{expected_commit}:{validator_name}"]) != validator_bytes:
        raise RecoveryPlanError("recovery validator differs from the expected commit")
    tree_files[validator_name] = validator_bytes
    return SourceSnapshot(
        commit=head,
        terraform_files=terraform_files,
        lockfile=lockfile,
        tree_sha256=_canonical_file_set_sha256(tree_files),
    )


def _initialize_tofu(*, tofu: Path, data_dir: Path) -> None:
    arguments = [
        str(tofu),
        f"-chdir={REHEARSAL_ROOT}",
        "init",
        "-backend=true",
        "-input=false",
        "-lockfile=readonly",
        "-reconfigure",
        *(
            f"-backend-config={name}={str(value).lower()}"
            for name, value in EXPECTED_BACKEND_CONFIG.items()
        ),
    ]
    result = _run_process(
        arguments, cwd=REPOSITORY_ROOT, timeout_seconds=300, data_dir=data_dir
    )
    if result.returncode != 0:
        raise RecoveryPlanError("the isolated backend could not be initialized")


def _backend_snapshot(*, tofu: Path, data_dir: Path) -> str:
    path = data_dir / "terraform.tfstate"
    value = _read_protected_file(path, label="backend metadata", required_mode=0o600)
    document = _load_json_bytes(value, label="backend metadata")
    backend = document.get("backend")
    if not isinstance(backend, Mapping) or backend.get("type") != "azurerm":
        raise RecoveryPlanError("backend type is not the isolated AzureRM backend")
    config = backend.get("config")
    if not isinstance(config, Mapping):
        raise RecoveryPlanError("backend configuration is missing")
    for name, expected in EXPECTED_BACKEND_CONFIG.items():
        if config.get(name) != expected:
            raise RecoveryPlanError("backend coordinates are not exact")
    for name, setting in config.items():
        if name not in EXPECTED_BACKEND_CONFIG and setting not in (None, "", False):
            raise RecoveryPlanError("backend configuration contains an override")
    workspace = _run_process(
        [str(tofu), f"-chdir={REHEARSAL_ROOT}", "workspace", "show"],
        cwd=REPOSITORY_ROOT,
        timeout_seconds=30,
        max_output_bytes=1024,
        data_dir=data_dir,
    )
    if workspace.returncode != 0 or workspace.stdout.strip() != b"default":
        raise RecoveryPlanError("the isolated backend workspace is not default")
    return _sha256_bytes(value)


def _normalize_boolean(value: Any) -> Any:
    if isinstance(value, str) and value in {"true", "false"}:
        return value == "true"
    return value


def _bounded_names(values: set[str]) -> str:
    names = sorted(values)
    displayed = names[:8]
    suffix = "" if len(names) <= len(displayed) else f" (+{len(names) - 8} more)"
    return ", ".join(displayed) + suffix


def _state_address(resource: Mapping[str, Any], instance: Mapping[str, Any]) -> str:
    mode = resource.get("mode")
    resource_type = resource.get("type")
    name = resource.get("name")
    module = resource.get("module")
    if mode not in {"managed", "data"}:
        raise RecoveryPlanError("state contains a resource with an invalid mode")
    if not isinstance(resource_type, str) or not isinstance(name, str):
        raise RecoveryPlanError("state contains a resource with an invalid address")
    address = f"{resource_type}.{name}"
    if mode == "data":
        address = f"data.{address}"
    if module is not None:
        if not isinstance(module, str):
            raise RecoveryPlanError("state contains an invalid module address")
        address = f"{module}.{address}"
    if "index_key" in instance:
        key = instance["index_key"]
        if isinstance(key, bool) or not isinstance(key, (str, int)):
            raise RecoveryPlanError("state contains an invalid instance key")
        address = f"{address}[{json.dumps(key, separators=(',', ':'))}]"
    return address


def _validate_state(
    state: Mapping[str, Any], *, label: str
) -> tuple[str, int, Mapping[str, tuple[str, int, str]]]:
    if state.get("lineage") != EXPECTED_LINEAGE:
        raise RecoveryPlanError(
            f"{label} lineage does not match the isolated rehearsal"
        )
    serial = state.get("serial")
    if type(serial) is not int or serial != EXPECTED_POST_UNTAINT_SERIAL:
        raise RecoveryPlanError(f"{label} serial must be exactly 8")
    if state.get("terraform_version") != EXPECTED_TOFU_VERSION:
        raise RecoveryPlanError(f"{label} OpenTofu version is not pinned")

    resources = state.get("resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise RecoveryPlanError(f"{label} resources are invalid")
    inventory: dict[str, tuple[str, int, str]] = {}
    for resource in resources:
        if not isinstance(resource, Mapping):
            raise RecoveryPlanError(f"{label} contains an invalid resource")
        instances = resource.get("instances")
        if not isinstance(instances, Sequence) or isinstance(instances, (str, bytes)):
            raise RecoveryPlanError(f"{label} contains invalid instances")
        provider = resource.get("provider")
        if not isinstance(provider, str):
            raise RecoveryPlanError(f"{label} contains an invalid provider address")
        for instance in instances:
            if not isinstance(instance, Mapping):
                raise RecoveryPlanError(f"{label} contains an invalid instance")
            if instance.get("status") not in {None, "ready"}:
                raise RecoveryPlanError(f"{label} contains a non-ready instance")
            if instance.get("deposed") is not None:
                raise RecoveryPlanError(f"{label} contains a deposed instance")
            schema_version = instance.get("schema_version")
            if isinstance(schema_version, bool) or not isinstance(schema_version, int):
                raise RecoveryPlanError(f"{label} contains an invalid schema version")
            attributes = instance.get("attributes")
            if not isinstance(attributes, Mapping):
                raise RecoveryPlanError(f"{label} contains invalid attributes")
            resource_id = attributes.get("id")
            if not isinstance(resource_id, str) or not resource_id:
                raise RecoveryPlanError(f"{label} contains an invalid resource ID")
            address = _state_address(resource, instance)
            if address in inventory:
                raise RecoveryPlanError(f"{label} contains duplicate addresses")
            inventory[address] = (provider, schema_version, resource_id)

    address_set = set(inventory)
    if address_set != EXPECTED_EXISTING_ADDRESSES:
        missing = set(EXPECTED_EXISTING_ADDRESSES) - address_set
        extra = address_set - set(EXPECTED_EXISTING_ADDRESSES)
        raise RecoveryPlanError(
            f"{label} address set differs from the recovery inventory; "
            f"missing={_bounded_names(missing)}; extra={_bounded_names(extra)}"
        )
    return EXPECTED_LINEAGE, serial, inventory


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise RecoveryPlanError(
            "saved plan archive is missing required content"
        ) from exc
    if info.flag_bits & 0x1:
        raise RecoveryPlanError("saved plan archive contains encrypted content")
    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise RecoveryPlanError("saved plan archive contains a symbolic link")
    if info.file_size <= 0 or info.file_size > MAXIMUM_ARTIFACT_BYTES:
        raise RecoveryPlanError("saved plan archive member size is invalid")
    try:
        value = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise RecoveryPlanError("saved plan archive could not be read") from exc
    if len(value) != info.file_size:
        raise RecoveryPlanError("saved plan archive member is incomplete")
    return value


def _inspect_plan_archive(
    plan_bytes: bytes, *, source: SourceSnapshot
) -> tuple[bytes, Mapping[str, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(plan_bytes)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise RecoveryPlanError("saved plan archive contains duplicate members")
            for info in infos:
                file_type = (info.external_attr >> 16) & 0o170000
                if info.flag_bits & 0x1 or file_type == stat.S_IFLNK:
                    raise RecoveryPlanError("saved plan archive member is unsafe")
                if info.file_size <= 0 or info.file_size > MAXIMUM_ARTIFACT_BYTES:
                    raise RecoveryPlanError("saved plan archive member size is invalid")
            if sum(info.file_size for info in infos) > MAXIMUM_PLAN_ARCHIVE_BYTES:
                raise RecoveryPlanError(
                    "saved plan archive expands beyond the size limit"
                )
            expected_config_names = {
                f"tfconfig/m-/{name}" for name in source.terraform_files
            }
            expected_names = expected_config_names | {
                "tfplan",
                "tfstate",
                "tfstate-prev",
                "tfconfig/modules.json",
                ".terraform.lock.hcl",
            }
            if set(names) != expected_names:
                raise RecoveryPlanError(
                    "saved plan archive contents differ from the pinned root"
                )
            if archive.testzip() is not None:
                raise RecoveryPlanError("saved plan archive failed its CRC check")
            try:
                modules = json.loads(_read_zip_member(archive, "tfconfig/modules.json"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RecoveryPlanError(
                    "saved plan module manifest is invalid"
                ) from exc
            if modules != [{"Key": "", "Dir": "."}]:
                raise RecoveryPlanError("saved plan module manifest is not exact")
            for name, expected in source.terraform_files.items():
                if _read_zip_member(archive, f"tfconfig/m-/{name}") != expected:
                    raise RecoveryPlanError(
                        "saved plan Terraform source differs from the reviewed checkout"
                    )
            if _read_zip_member(archive, ".terraform.lock.hcl") != source.lockfile:
                raise RecoveryPlanError(
                    "saved plan provider lockfile differs from the reviewed checkout"
                )
            embedded_state_bytes = _read_zip_member(archive, "tfstate")
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise RecoveryPlanError(
            "saved plan is not a valid OpenTofu plan archive"
        ) from exc
    embedded_state = _load_json_bytes(
        embedded_state_bytes, label="saved plan embedded state"
    )
    return embedded_state_bytes, embedded_state


def _module_addresses(module: Mapping[str, Any]) -> set[str]:
    addresses: set[str] = set()
    resources = module.get("resources", [])
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise RecoveryPlanError("plan prior-state resources are invalid")
    for resource in resources:
        if not isinstance(resource, Mapping):
            raise RecoveryPlanError("plan prior state contains an invalid resource")
        address = resource.get("address")
        if not isinstance(address, str):
            raise RecoveryPlanError("plan prior state contains an invalid address")
        addresses.add(address)
    children = module.get("child_modules", [])
    if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
        raise RecoveryPlanError("plan prior-state child modules are invalid")
    for child in children:
        if not isinstance(child, Mapping):
            raise RecoveryPlanError("plan prior state contains an invalid module")
        addresses.update(_module_addresses(child))
    return addresses


def _validate_provider_configuration(plan: Mapping[str, Any]) -> None:
    configuration = plan.get("configuration")
    if not isinstance(configuration, Mapping):
        raise RecoveryPlanError("plan configuration is missing")
    provider_config = configuration.get("provider_config")
    if not isinstance(provider_config, Mapping):
        raise RecoveryPlanError("plan provider configuration is missing")
    if set(provider_config) != {"azurerm", "azapi", "terraform"}:
        raise RecoveryPlanError("plan provider set is not exact")
    azurerm = provider_config.get("azurerm")
    azapi = provider_config.get("azapi")
    builtin = provider_config.get("terraform")
    if not all(isinstance(value, Mapping) for value in (azurerm, azapi, builtin)):
        raise RecoveryPlanError("plan provider configuration is invalid")
    assert isinstance(azurerm, Mapping)
    assert isinstance(azapi, Mapping)
    assert isinstance(builtin, Mapping)
    expected_providers = {
        "azurerm": (azurerm, "registry.opentofu.org/hashicorp/azurerm", "4.73.0"),
        "azapi": (azapi, "registry.opentofu.org/azure/azapi", "2.1.0"),
        "terraform": (builtin, "terraform.io/builtin/terraform", None),
    }
    for name, (provider, full_name, version) in expected_providers.items():
        if provider.get("full_name") != full_name:
            raise RecoveryPlanError(f"{name} provider source is not pinned")
        if provider.get("version_constraint") != version:
            raise RecoveryPlanError(f"{name} provider version is not pinned")

    expressions = azurerm.get("expressions")
    if not isinstance(expressions, Mapping):
        raise RecoveryPlanError("AzureRM provider expressions are missing")
    features = expressions.get("features")
    if (
        not isinstance(features, Sequence)
        or isinstance(features, (str, bytes))
        or len(features) != 1
        or not isinstance(features[0], Mapping)
    ):
        raise RecoveryPlanError(
            "AzureRM storage data-plane opt-out is missing from the saved plan"
        )
    feature = features[0]
    assert isinstance(feature, Mapping)
    storage = feature.get("storage")
    if (
        not isinstance(storage, Sequence)
        or isinstance(storage, (str, bytes))
        or len(storage) != 1
        or not isinstance(storage[0], Mapping)
    ):
        raise RecoveryPlanError(
            "AzureRM storage data-plane opt-out is missing from the saved plan"
        )
    storage_feature = storage[0]
    assert isinstance(storage_feature, Mapping)
    data_plane = storage_feature.get("data_plane_available")
    if (
        not isinstance(data_plane, Mapping)
        or data_plane.get("constant_value") is not False
    ):
        raise RecoveryPlanError("AzureRM storage data plane must be disabled")


def _validate_variables(plan: Mapping[str, Any]) -> None:
    variables = plan.get("variables")
    if not isinstance(variables, Mapping):
        raise RecoveryPlanError("plan variables are missing")
    if set(variables) != set(EXPECTED_VARIABLES):
        raise RecoveryPlanError("plan variable names differ from the base recovery set")
    for name, expected in EXPECTED_VARIABLES.items():
        record = variables[name]
        if not isinstance(record, Mapping) or "value" not in record:
            raise RecoveryPlanError(f"plan variable {name} has no fixed value")
        if _normalize_boolean(record["value"]) != expected:
            raise RecoveryPlanError(f"plan variable {name} is not recovery-safe")


def _validate_checks(plan: Mapping[str, Any]) -> None:
    checks = plan.get("checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise RecoveryPlanError("plan check results are missing")
    if not checks:
        raise RecoveryPlanError("plan contains no evaluated checks")
    for check in checks:
        if not isinstance(check, Mapping) or check.get("status") != "pass":
            raise RecoveryPlanError("plan contains a non-passing check")
        instances = check.get("instances", [])
        if not isinstance(instances, Sequence) or isinstance(instances, (str, bytes)):
            raise RecoveryPlanError("plan contains invalid check instances")
        for instance in instances:
            if not isinstance(instance, Mapping) or instance.get("status") != "pass":
                raise RecoveryPlanError("plan contains a non-passing check instance")
            if instance.get("problems") not in (None, []):
                raise RecoveryPlanError("plan check instance reports a problem")


def _validate_plan_timestamp(plan: Mapping[str, Any], *, now: datetime) -> str:
    raw_timestamp = plan.get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.endswith("Z"):
        raise RecoveryPlanError("plan timestamp is missing or not UTC")
    try:
        timestamp = datetime.fromisoformat(raw_timestamp[:-1] + "+00:00")
    except ValueError as exc:
        raise RecoveryPlanError("plan timestamp is invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise RecoveryPlanError("plan timestamp is not UTC")
    normalized_now = now.astimezone(UTC)
    if timestamp > normalized_now + MAXIMUM_CLOCK_SKEW:
        raise RecoveryPlanError("plan timestamp is in the future")
    if normalized_now - timestamp > MAXIMUM_PLAN_AGE:
        raise RecoveryPlanError("saved plan is stale")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_plan(plan: Mapping[str, Any], *, now: datetime) -> str:
    if plan.get("format_version") != EXPECTED_FORMAT_VERSION:
        raise RecoveryPlanError("plan JSON format version is not pinned")
    if plan.get("terraform_version") != EXPECTED_TOFU_VERSION:
        raise RecoveryPlanError("plan OpenTofu version is not pinned")
    if plan.get("errored") is not False:
        raise RecoveryPlanError("plan is errored or does not prove success")
    for optional_field in ("applyable", "complete"):
        if optional_field in plan and plan[optional_field] is not True:
            raise RecoveryPlanError(f"plan reports {optional_field}=false")
    if plan.get("resource_drift") not in (None, []):
        raise RecoveryPlanError("plan reports resource drift")
    if plan.get("deferred_changes") not in (None, []):
        raise RecoveryPlanError("plan contains deferred changes")
    if plan.get("action_invocations") not in (None, []):
        raise RecoveryPlanError("plan contains action invocations")

    timestamp = _validate_plan_timestamp(plan, now=now)
    _validate_provider_configuration(plan)
    _validate_variables(plan)
    _validate_checks(plan)

    prior_state = plan.get("prior_state")
    if not isinstance(prior_state, Mapping):
        raise RecoveryPlanError("plan prior state is missing")
    values = prior_state.get("values")
    if not isinstance(values, Mapping):
        raise RecoveryPlanError("plan prior-state values are missing")
    root_module = values.get("root_module")
    if not isinstance(root_module, Mapping):
        raise RecoveryPlanError("plan prior-state root module is missing")
    if _module_addresses(root_module) != set(EXPECTED_EXISTING_ADDRESSES):
        raise RecoveryPlanError("plan prior state differs from recovery inventory")

    changes = plan.get("resource_changes")
    if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes)):
        raise RecoveryPlanError("plan resource changes are missing")
    observed: dict[str, tuple[str, ...]] = {}
    for resource_change in changes:
        if not isinstance(resource_change, Mapping):
            raise RecoveryPlanError("plan contains an invalid resource change")
        address = resource_change.get("address")
        if not isinstance(address, str):
            raise RecoveryPlanError("plan contains an invalid resource address")
        if address in observed:
            raise RecoveryPlanError("plan contains a duplicate resource change")
        change = resource_change.get("change")
        if not isinstance(change, Mapping):
            raise RecoveryPlanError("plan contains an invalid action body")
        actions = change.get("actions")
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
            raise RecoveryPlanError("plan contains invalid actions")
        if not all(isinstance(action, str) for action in actions):
            raise RecoveryPlanError("plan contains a non-string action")
        observed[address] = tuple(actions)

    expected_addresses = EXPECTED_EXISTING_ADDRESSES | EXPECTED_CREATE_ADDRESSES
    if set(observed) != set(expected_addresses):
        raise RecoveryPlanError(
            "plan resource address set is not the exact recovery set"
        )
    for address in EXPECTED_EXISTING_ADDRESSES:
        if observed[address] != ("no-op",):
            raise RecoveryPlanError(f"existing resource would change: {address}")
    for address in EXPECTED_CREATE_ADDRESSES:
        if observed[address] != ("create",):
            raise RecoveryPlanError(f"missing resource is not create-only: {address}")
    return timestamp


def _validate_captured_artifacts(
    *,
    saved_plan_bytes: bytes,
    state_bytes: bytes,
    source: SourceSnapshot,
    backend_metadata_sha256: str,
    tofu: Path,
    data_dir: Path,
    generation_mode: str,
    now: datetime,
    expected_saved_plan_sha256: str | None = None,
) -> Mapping[str, Any]:
    saved_plan_sha256 = _sha256_bytes(saved_plan_bytes)
    if (
        expected_saved_plan_sha256 is not None
        and saved_plan_sha256 != expected_saved_plan_sha256
    ):
        raise RecoveryPlanError("saved plan digest does not match reviewed evidence")

    external_state = _load_json_bytes(state_bytes, label="state snapshot")
    lineage, serial, external_inventory = _validate_state(
        external_state, label="state snapshot"
    )
    embedded_state_bytes, embedded_state = _inspect_plan_archive(
        saved_plan_bytes, source=source
    )
    embedded_lineage, embedded_serial, embedded_inventory = _validate_state(
        embedded_state, label="saved plan embedded state"
    )
    if (embedded_lineage, embedded_serial, embedded_inventory) != (
        lineage,
        serial,
        external_inventory,
    ):
        raise RecoveryPlanError(
            "saved plan embedded state does not match the protected snapshot"
        )

    plan_json_bytes = _show_saved_plan(saved_plan_bytes, tofu=tofu, data_dir=data_dir)
    plan = _load_json_bytes(plan_json_bytes, label="derived plan JSON")
    plan_timestamp = _validate_plan(plan, now=now)
    return {
        "schema_version": 2,
        "policy_id": "cosmaneura-staging-rehearsal-base-recovery-plan-v2",
        "generation_mode": generation_mode,
        "source_commit": source.commit,
        "source_tree_sha256": source.tree_sha256,
        "provider_lock_sha256": _sha256_bytes(source.lockfile),
        "backend_metadata_sha256": backend_metadata_sha256,
        "backend_workspace": "default",
        "state_lineage": lineage,
        "state_serial": serial,
        "state_json_sha256": _sha256_bytes(state_bytes),
        "embedded_state_sha256": _sha256_bytes(embedded_state_bytes),
        "saved_plan_sha256": saved_plan_sha256,
        "derived_plan_json_sha256": _sha256_bytes(plan_json_bytes),
        "plan_timestamp": plan_timestamp,
        "open_tofu_version": EXPECTED_TOFU_VERSION,
        "validated_nonempty_change_set": True,
        "artifact_policy_complete": True,
        "live_readback_bound": False,
        "apply_authorized": False,
        "full_refresh_generated_here": generation_mode == "validator_full_refresh",
        "existing_resource_count": len(EXPECTED_EXISTING_ADDRESSES),
        "create_count": len(EXPECTED_CREATE_ADDRESSES),
        "update_count": 0,
        "destroy_or_replace_count": 0,
        "pass": True,
    }


def create_and_validate_recovery_plan(
    *, saved_plan: Path, state_json: Path, expected_source_commit: str
) -> Mapping[str, Any]:
    _reject_symlink_components(saved_plan, label="saved plan", allow_missing_leaf=True)
    if saved_plan.exists():
        raise RecoveryPlanError("saved plan output already exists")
    _require_protected_directory(saved_plan.parent, label="saved plan directory")
    state_bytes = _read_protected_file(
        state_json, label="state snapshot", required_mode=0o600
    )
    source_before = _source_snapshot(expected_source_commit)
    tofu = _resolve_and_verify_tofu()
    with tempfile.TemporaryDirectory(
        prefix=".cosmaneura-tofu-data-", dir=saved_plan.parent
    ) as temporary_data_dir:
        data_dir = Path(temporary_data_dir)
        _initialize_tofu(tofu=tofu, data_dir=data_dir)
        backend_before = _backend_snapshot(tofu=tofu, data_dir=data_dir)
        saved_plan_bytes = _generate_saved_plan(
            tofu=tofu, output_parent=saved_plan.parent, data_dir=data_dir
        )
        receipt = _validate_captured_artifacts(
            saved_plan_bytes=saved_plan_bytes,
            state_bytes=state_bytes,
            source=source_before,
            backend_metadata_sha256=backend_before,
            tofu=tofu,
            data_dir=data_dir,
            generation_mode="validator_full_refresh",
            now=datetime.now(UTC),
        )
        if _backend_snapshot(tofu=tofu, data_dir=data_dir) != backend_before:
            raise RecoveryPlanError(
                "backend metadata changed while the plan was generated"
            )
    if _source_snapshot(expected_source_commit) != source_before:
        raise RecoveryPlanError("recovery source changed while the plan was generated")
    _write_protected_file(saved_plan, saved_plan_bytes, label="saved plan")
    if (
        _sha256_bytes(
            _read_protected_file(saved_plan, label="saved plan", required_mode=0o400)
        )
        != receipt["saved_plan_sha256"]
    ):
        raise RecoveryPlanError("saved plan output does not match validated bytes")
    return receipt


def revalidate_recovery_plan(
    *,
    saved_plan: Path,
    state_json: Path,
    execution_dir: Path,
    expected_source_commit: str,
    expected_saved_plan_sha256: str,
) -> Mapping[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_saved_plan_sha256) is None:
        raise RecoveryPlanError("expected saved-plan digest is invalid")
    saved_plan_bytes = _read_protected_file(
        saved_plan, label="saved plan", required_mode=0o400
    )
    state_bytes = _read_protected_file(
        state_json, label="state snapshot", required_mode=0o600
    )
    if execution_dir.parent != saved_plan.parent:
        raise RecoveryPlanError("execution directory must share the saved-plan parent")
    _create_protected_directory(execution_dir, label="execution directory")
    source_before = _source_snapshot(expected_source_commit)
    tofu = _resolve_and_verify_tofu()
    _initialize_tofu(tofu=tofu, data_dir=execution_dir)
    backend_before = _backend_snapshot(tofu=tofu, data_dir=execution_dir)
    receipt = dict(
        _validate_captured_artifacts(
            saved_plan_bytes=saved_plan_bytes,
            state_bytes=state_bytes,
            source=source_before,
            backend_metadata_sha256=backend_before,
            tofu=tofu,
            data_dir=execution_dir,
            generation_mode="digest_bound_revalidation",
            now=datetime.now(UTC),
            expected_saved_plan_sha256=expected_saved_plan_sha256,
        )
    )
    if _backend_snapshot(tofu=tofu, data_dir=execution_dir) != backend_before:
        raise RecoveryPlanError("backend metadata changed during plan revalidation")
    if _source_snapshot(expected_source_commit) != source_before:
        raise RecoveryPlanError("recovery source changed during plan revalidation")
    receipt.update(
        execution_data_dir=str(execution_dir),
        execution_provider_tree_sha256=_provider_tree_sha256(execution_dir),
        open_tofu_path=str(tofu),
        open_tofu_sha256=_executable_sha256(tofu, label="OpenTofu executable"),
    )
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("create", "verify"))
    parser.add_argument("--saved-plan", required=True, type=Path)
    parser.add_argument("--state-json", required=True, type=Path)
    parser.add_argument("--execution-dir", type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-saved-plan-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "create":
            if args.expected_saved_plan_sha256 is not None or args.execution_dir:
                raise RecoveryPlanError(
                    "create mode does not accept a plan digest or execution directory"
                )
            receipt = create_and_validate_recovery_plan(
                saved_plan=args.saved_plan,
                state_json=args.state_json,
                expected_source_commit=args.expected_source_commit,
            )
        else:
            if args.expected_saved_plan_sha256 is None or args.execution_dir is None:
                raise RecoveryPlanError(
                    "verify mode requires the reviewed plan digest and new execution directory"
                )
            receipt = revalidate_recovery_plan(
                saved_plan=args.saved_plan,
                state_json=args.state_json,
                execution_dir=args.execution_dir,
                expected_source_commit=args.expected_source_commit,
                expected_saved_plan_sha256=args.expected_saved_plan_sha256,
            )
    except RecoveryPlanError as exc:
        print(f"recovery_plan_refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
