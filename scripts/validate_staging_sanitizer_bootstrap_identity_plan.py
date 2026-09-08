#!/usr/bin/env python3
"""Validate an exact sanitizer-bootstrap identity plan without authorizing apply."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import selectors
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
IDENTITY_ROOT = REPOSITORY_ROOT / "infra-staging-sanitizer-bootstrap-identity"
VALIDATOR_PATH = Path(__file__).resolve()

EXPECTED_FORMAT_VERSION = "1.2"
EXPECTED_TOFU_VERSION = "1.12.5"
EXPECTED_SUBSCRIPTION_ID = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
EXPECTED_TENANT_ID = "d51754d0-bba5-4810-820e-afac542844d6"
EXPECTED_ORIGIN_URL = "git@github.com:CosmaNeura/cosmaneura.git"
EXPECTED_RESOURCE_GROUP = "cosmaneura-staging-rehearsal-control"
EXPECTED_LOCATION = "eastus"
EXPECTED_IDENTITY_NAME = "cosmaneura-staging-restore-bootstrap-job"
EXPECTED_STATE_BOUNDARY: Mapping[str, str] = {
    "resource_group": "cosmaneura-tfstate",
    "storage_account": "cosmaneuratfstate",
    "container": "cosmaneura-tfstate",
    "key": "staging-sanitizer-bootstrap-identity.tfstate",
}
EXPECTED_BASE_TAGS: Mapping[str, str] = {
    "data-classification": "non-phi-control-metadata",
    "environment": "staging",
    "lifecycle": "ephemeral",
    "managed-by": "opentofu",
    "purpose": "staging-restore-sanitizer-bootstrap",
}
EXPECTED_VARIABLES = frozenset(
    {
        "enable_sanitizer_bootstrap_identity",
        "sanitizer_bootstrap_identity_plan_approved",
        "sanitizer_bootstrap_lifetime_hours",
        "sanitizer_bootstrap_rehearsal_id",
    }
)
EXPECTED_CHANGE_ADDRESSES = frozenset(
    {
        "azurerm_user_assigned_identity.sanitizer_bootstrap[0]",
        "terraform_data.activation_guard[0]",
    }
)
MAXIMUM_ARTIFACT_BYTES = 64 * 1024 * 1024
MAXIMUM_PLAN_JSON_BYTES = 16 * 1024 * 1024
MAXIMUM_PLAN_ARCHIVE_BYTES = 128 * 1024 * 1024
MAXIMUM_CLOCK_SKEW = timedelta(minutes=5)
SYSTEM_GIT = Path("/usr/bin/git")


class BootstrapPlanError(ValueError):
    """Raised when a saved plan cannot satisfy the non-authoritative policy."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapPlanError("JSON contains duplicate object keys")
        result[key] = value
    return result


def _reject_nonfinite_json(_value: str) -> None:
    raise BootstrapPlanError("JSON contains a non-finite number")


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


def _reject_symlink_components(path: Path, *, label: str) -> None:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise BootstrapPlanError(f"{label} path must be absolute and normalized")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise BootstrapPlanError(f"{label} path is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise BootstrapPlanError(f"{label} path contains a symbolic link")


def _open_protected_directory(path: Path, *, label: str) -> int:
    _reject_symlink_components(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise BootstrapPlanError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise BootstrapPlanError(
            f"{label} must be an owner-controlled directory with mode 0700"
        )
    return descriptor


def _require_protected_directory(path: Path, *, label: str) -> None:
    os.close(_open_protected_directory(path, label=label))


def _read_protected_file(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    parent_descriptor = _open_protected_directory(
        path.parent, label=f"{label} directory"
    )
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise BootstrapPlanError(f"{label} is unavailable") from exc
    finally:
        os.close(parent_descriptor)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_size <= 0
            or before.st_size > MAXIMUM_ARTIFACT_BYTES
        ):
            raise BootstrapPlanError(
                f"{label} must be one owner-controlled 0400 regular file"
            )
        value = bytearray()
        while len(value) < before.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, before.st_size - len(value)))
            if not chunk:
                break
            value.extend(chunk)
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
            raise BootstrapPlanError(f"{label} changed while it was read")
        return bytes(value)
    except OSError as exc:
        raise BootstrapPlanError(f"{label} could not be read") from exc
    finally:
        os.close(descriptor)


def _load_json_bytes(value: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BootstrapPlanError(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise BootstrapPlanError(f"{label} must contain a JSON object")
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
    environment["TF_IN_AUTOMATION"] = "1"
    if data_dir is not None:
        environment["TF_DATA_DIR"] = str(data_dir)
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
        raise BootstrapPlanError("required local command did not start") from exc
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
                raise BootstrapPlanError("required local command timed out")
            for key, _ in selector.select(min(remaining, 0.5)):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > max_output_bytes:
                    raise BootstrapPlanError(
                        "required local command output exceeded limit"
                    )
                streams[key.data].append(chunk)
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired, BootstrapPlanError) as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.stdout.close()
        process.stderr.close()
        process.wait()
        if isinstance(exc, BootstrapPlanError):
            raise
        raise BootstrapPlanError("required local command did not complete") from exc
    finally:
        selector.close()
    stdout = b"".join(streams[stdout_fd])
    stderr = b"".join(streams[stderr_fd])
    process.stdout.close()
    process.stderr.close()
    return subprocess.CompletedProcess(list(arguments), returncode, stdout, stderr)


def _trusted_executable(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise BootstrapPlanError(f"the {label} executable is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise BootstrapPlanError(f"the {label} executable is not trusted")
    return resolved


def _executable_sha256(path: Path, *, label: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o111:
            raise BootstrapPlanError(f"the {label} executable is invalid")
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
            raise BootstrapPlanError(f"the {label} executable changed while hashed")
        return digest.hexdigest()
    except OSError as exc:
        raise BootstrapPlanError(f"the {label} executable could not be hashed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _resolve_and_verify_tofu() -> Path:
    executable = shutil.which("tofu")
    if executable is None:
        raise BootstrapPlanError("the pinned OpenTofu executable is unavailable")
    tofu = _trusted_executable(Path(executable), label="OpenTofu")
    result = _run_process(
        [str(tofu), "version", "-json"], cwd=REPOSITORY_ROOT, timeout_seconds=30
    )
    version = _load_json_bytes(result.stdout, label="OpenTofu version output")
    if (
        result.returncode != 0
        or version.get("terraform_version") != EXPECTED_TOFU_VERSION
    ):
        raise BootstrapPlanError("the OpenTofu executable version is not pinned")
    return tofu


def _run_git(arguments: Sequence[str]) -> bytes:
    git = _trusted_executable(SYSTEM_GIT, label="Git")
    result = _run_process(
        [str(git), "--no-replace-objects", "-C", str(REPOSITORY_ROOT), *arguments],
        cwd=REPOSITORY_ROOT,
        timeout_seconds=30,
    )
    if result.returncode != 0:
        raise BootstrapPlanError("the source checkout could not be verified")
    return result.stdout


def _source_snapshot(expected_commit: str) -> SourceSnapshot:
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        raise BootstrapPlanError("expected source commit is not a full lowercase SHA")
    head = _run_git(["rev-parse", "HEAD"]).decode("ascii").strip()
    remote_head = (
        _run_git(["rev-parse", "refs/remotes/origin/master"]).decode("ascii").strip()
    )
    origin = _run_git(["remote", "get-url", "--all", "origin"]).decode("ascii")
    if (
        head != expected_commit
        or remote_head != expected_commit
        or origin != f"{EXPECTED_ORIGIN_URL}\n"
    ):
        raise BootstrapPlanError(
            "source checkout is not the expected local origin/master commit"
        )
    relevant_paths = (
        str(IDENTITY_ROOT.relative_to(REPOSITORY_ROOT)),
        str(VALIDATOR_PATH.relative_to(REPOSITORY_ROOT)),
    )
    if _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all", "--", *relevant_paths]
    ):
        raise BootstrapPlanError("bootstrap plan source paths are not clean")

    terraform_files: dict[str, bytes] = {}
    for path in sorted(IDENTITY_ROOT.glob("*.tf")):
        if path.is_symlink() or not path.is_file():
            raise BootstrapPlanError("bootstrap Terraform source is invalid")
        terraform_files[path.name] = path.read_bytes()
    if not terraform_files:
        raise BootstrapPlanError("bootstrap Terraform source is missing")
    lock_path = IDENTITY_ROOT / ".terraform.lock.hcl"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise BootstrapPlanError("bootstrap provider lockfile is missing")
    lockfile = lock_path.read_bytes()
    source_root = IDENTITY_ROOT.relative_to(REPOSITORY_ROOT)
    expected_names = set(
        _run_git(
            ["ls-tree", "-r", "--name-only", expected_commit, "--", str(source_root)]
        )
        .decode("utf-8")
        .splitlines()
    )
    tree_files = {**terraform_files, ".terraform.lock.hcl": lockfile}
    actual_names = {str(source_root / name) for name in tree_files}
    if actual_names != {
        name for name in expected_names if name.endswith((".tf", ".terraform.lock.hcl"))
    }:
        raise BootstrapPlanError("bootstrap source file set differs from the commit")
    for name, value in tree_files.items():
        if _run_git(["show", f"{expected_commit}:{source_root / name}"]) != value:
            raise BootstrapPlanError("bootstrap source differs from the commit")
    validator_name = str(VALIDATOR_PATH.relative_to(REPOSITORY_ROOT))
    validator_bytes = VALIDATOR_PATH.read_bytes()
    if _run_git(["show", f"{expected_commit}:{validator_name}"]) != validator_bytes:
        raise BootstrapPlanError("bootstrap plan validator differs from the commit")
    tree_files[validator_name] = validator_bytes
    return SourceSnapshot(
        commit=head,
        terraform_files=terraform_files,
        lockfile=lockfile,
        tree_sha256=_canonical_file_set_sha256(tree_files),
    )


def _provider_tree_sha256(data_dir: Path) -> str:
    providers = sorted((data_dir / "providers").rglob("terraform-provider-*"))
    if len(providers) != 1 or providers[0].is_symlink():
        raise BootstrapPlanError("offline decoder provider set is not exact")
    relative = providers[0].relative_to(data_dir / "providers")
    expected_prefix = Path("registry.opentofu.org/hashicorp/azurerm/4.73.0")
    if relative.parts[: len(expected_prefix.parts)] != expected_prefix.parts:
        raise BootstrapPlanError("offline decoder provider path is not pinned")
    provider = _trusted_executable(providers[0], label="AzureRM provider")
    return _executable_sha256(provider, label="AzureRM provider")


def _initialize_decoder(*, tofu: Path, data_dir: Path) -> str:
    result = _run_process(
        [
            str(tofu),
            f"-chdir={IDENTITY_ROOT}",
            "init",
            "-backend=false",
            "-input=false",
            "-lockfile=readonly",
            "-no-color",
        ],
        cwd=REPOSITORY_ROOT,
        timeout_seconds=300,
        data_dir=data_dir,
    )
    if result.returncode != 0:
        raise BootstrapPlanError("the offline plan decoder could not be initialized")
    if (data_dir / "terraform.tfstate").exists():
        raise BootstrapPlanError("offline decoder unexpectedly initialized a backend")
    return _provider_tree_sha256(data_dir)


def _show_saved_plan(plan_bytes: bytes, *, tofu: Path, data_dir: Path) -> bytes:
    with tempfile.TemporaryFile(prefix="cosmaneura-bootstrap-plan-") as plan_file:
        plan_file.write(plan_bytes)
        plan_file.flush()
        plan_file.seek(0)
        descriptor = plan_file.fileno()
        result = _run_process(
            [
                str(tofu),
                f"-chdir={IDENTITY_ROOT}",
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
        raise BootstrapPlanError("the saved plan could not be decoded by OpenTofu")
    if not result.stdout or len(result.stdout) > MAXIMUM_PLAN_JSON_BYTES:
        raise BootstrapPlanError("derived plan JSON size is outside the accepted range")
    return result.stdout


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise BootstrapPlanError(
            "saved plan archive is missing required content"
        ) from exc
    file_type = (info.external_attr >> 16) & 0o170000
    if info.flag_bits & 0x1 or file_type == stat.S_IFLNK:
        raise BootstrapPlanError("saved plan archive member is unsafe")
    if info.file_size <= 0 or info.file_size > MAXIMUM_ARTIFACT_BYTES:
        raise BootstrapPlanError("saved plan archive member size is invalid")
    try:
        value = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BootstrapPlanError("saved plan archive could not be read") from exc
    if len(value) != info.file_size:
        raise BootstrapPlanError("saved plan archive member is incomplete")
    return value


def _inspect_plan_archive(
    plan_bytes: bytes, *, source: SourceSnapshot
) -> tuple[bytes, Mapping[str, Any], bytes, Mapping[str, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(plan_bytes)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise BootstrapPlanError("saved plan archive has duplicate members")
            for info in infos:
                file_type = (info.external_attr >> 16) & 0o170000
                if info.flag_bits & 0x1 or file_type == stat.S_IFLNK:
                    raise BootstrapPlanError("saved plan archive member is unsafe")
                if info.file_size <= 0 or info.file_size > MAXIMUM_ARTIFACT_BYTES:
                    raise BootstrapPlanError(
                        "saved plan archive member size is invalid"
                    )
            if sum(info.file_size for info in infos) > MAXIMUM_PLAN_ARCHIVE_BYTES:
                raise BootstrapPlanError("saved plan archive expands beyond the limit")
            expected_names = {
                *(f"tfconfig/m-/{name}" for name in source.terraform_files),
                "tfplan",
                "tfstate",
                "tfstate-prev",
                "tfconfig/modules.json",
                ".terraform.lock.hcl",
            }
            if set(names) != expected_names:
                raise BootstrapPlanError(
                    "saved plan archive contents differ from the pinned root"
                )
            if archive.testzip() is not None:
                raise BootstrapPlanError("saved plan archive failed its CRC check")
            modules = json.loads(
                _read_zip_member(archive, "tfconfig/modules.json"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_nonfinite_json,
            )
            if modules != [{"Key": "", "Dir": "."}]:
                raise BootstrapPlanError("saved plan module manifest is not exact")
            for name, expected in source.terraform_files.items():
                if _read_zip_member(archive, f"tfconfig/m-/{name}") != expected:
                    raise BootstrapPlanError(
                        "saved plan source differs from the reviewed checkout"
                    )
            if _read_zip_member(archive, ".terraform.lock.hcl") != source.lockfile:
                raise BootstrapPlanError(
                    "saved plan lockfile differs from the reviewed checkout"
                )
            previous_bytes = _read_zip_member(archive, "tfstate-prev")
            refreshed_bytes = _read_zip_member(archive, "tfstate")
    except (
        OSError,
        RuntimeError,
        UnicodeDecodeError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        raise BootstrapPlanError("saved plan is not a valid OpenTofu archive") from exc
    return (
        previous_bytes,
        _load_json_bytes(previous_bytes, label="embedded prior state"),
        refreshed_bytes,
        _load_json_bytes(refreshed_bytes, label="embedded refreshed state"),
    )


def _state_header(
    state: Mapping[str, Any], *, expected_lineage: str, expected_serial: int
) -> None:
    if set(state) != {
        "version",
        "terraform_version",
        "serial",
        "lineage",
        "outputs",
        "resources",
        "check_results",
    }:
        raise BootstrapPlanError("embedded state field set is not exact")
    if (
        state.get("version") != 4
        or state.get("terraform_version") != EXPECTED_TOFU_VERSION
        or state.get("lineage") != expected_lineage
        or type(state.get("serial")) is not int
        or state.get("serial") != expected_serial
        or state.get("check_results") is not None
    ):
        raise BootstrapPlanError("embedded state identity is not exact")


def _validate_resource_group_state(resource: Any) -> None:
    if not isinstance(resource, Mapping):
        raise BootstrapPlanError("refreshed resource-group state is invalid")
    expected_fields = {
        "mode": "data",
        "type": "azurerm_resource_group",
        "name": "control",
        "provider": 'provider["registry.opentofu.org/hashicorp/azurerm"]',
    }
    if any(resource.get(name) != value for name, value in expected_fields.items()):
        raise BootstrapPlanError("refreshed resource-group identity is not exact")
    instances = resource.get("instances")
    if not isinstance(instances, Sequence) or isinstance(instances, (str, bytes)):
        raise BootstrapPlanError("refreshed resource-group instance is missing")
    if len(instances) != 1 or not isinstance(instances[0], Mapping):
        raise BootstrapPlanError("refreshed resource-group instance is not exact")
    instance = instances[0]
    attributes = instance.get("attributes")
    expected_id = (
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
        f"{EXPECTED_RESOURCE_GROUP}"
    )
    if (
        instance.get("index_key") != 0
        or instance.get("schema_version") != 0
        or not isinstance(attributes, Mapping)
        or str(attributes.get("id", "")).lower() != expected_id.lower()
        or attributes.get("name") != EXPECTED_RESOURCE_GROUP
        or str(attributes.get("location", "")).lower() != EXPECTED_LOCATION
        or not isinstance(attributes.get("tags"), Mapping)
    ):
        raise BootstrapPlanError("refreshed resource-group attributes are not exact")


def _validate_embedded_states(
    previous: Mapping[str, Any],
    refreshed: Mapping[str, Any],
    *,
    expected_lineage: str,
    expected_serial: int,
) -> None:
    for state in (previous, refreshed):
        _state_header(
            state,
            expected_lineage=expected_lineage,
            expected_serial=expected_serial,
        )
    if previous.get("outputs") != {} or previous.get("resources") != []:
        raise BootstrapPlanError("embedded prior state is not empty")
    expected_output = {
        "state_boundary": {
            "value": dict(EXPECTED_STATE_BOUNDARY),
            "type": [
                "object",
                {
                    "container": "string",
                    "key": "string",
                    "resource_group": "string",
                    "storage_account": "string",
                },
            ],
        }
    }
    if refreshed.get("outputs") != expected_output:
        raise BootstrapPlanError("refreshed state boundary is not exact")
    resources = refreshed.get("resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise BootstrapPlanError("refreshed state resource set is invalid")
    if len(resources) != 1:
        raise BootstrapPlanError("refreshed state contains an unexpected resource")
    _validate_resource_group_state(resources[0])


def _normalized_bool(value: Any, *, label: str) -> bool:
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    raise BootstrapPlanError(f"plan variable {label} is not an exact boolean")


def _normalized_lifetime(value: Any) -> int:
    if isinstance(value, bool):
        raise BootstrapPlanError("plan lifetime is not an integer")
    if isinstance(value, int):
        lifetime = value
    elif isinstance(value, str) and re.fullmatch(r"(?:[1-9]|1[0-9]|2[0-4])", value):
        lifetime = int(value)
    else:
        raise BootstrapPlanError("plan lifetime is not a canonical integer")
    if not 1 <= lifetime <= 24:
        raise BootstrapPlanError("plan lifetime is outside 1 through 24 hours")
    return lifetime


def _plan_inputs(plan: Mapping[str, Any]) -> tuple[str, int]:
    variables = plan.get("variables")
    if not isinstance(variables, Mapping) or set(variables) != EXPECTED_VARIABLES:
        raise BootstrapPlanError("plan variable set is not exact")
    values: dict[str, Any] = {}
    for name, record in variables.items():
        if not isinstance(record, Mapping) or set(record) != {"value"}:
            raise BootstrapPlanError(f"plan variable {name} has no exact value")
        values[name] = record["value"]
    if not _normalized_bool(
        values["enable_sanitizer_bootstrap_identity"], label="enable"
    ) or not _normalized_bool(
        values["sanitizer_bootstrap_identity_plan_approved"], label="approval"
    ):
        raise BootstrapPlanError("plan enable and approval variables must both be true")
    rehearsal_id = values["sanitizer_bootstrap_rehearsal_id"]
    if (
        not isinstance(rehearsal_id, str)
        or re.fullmatch(r"[0-9a-f]{12}", rehearsal_id) is None
    ):
        raise BootstrapPlanError("plan rehearsal ID is not exact")
    return rehearsal_id, _normalized_lifetime(
        values["sanitizer_bootstrap_lifetime_hours"]
    )


def _plan_times(
    plan: Mapping[str, Any], *, lifetime_hours: int, now: datetime
) -> tuple[str, str]:
    raw = plan.get("timestamp")
    if (
        not isinstance(raw, str)
        or re.fullmatch(
            r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", raw
        )
        is None
    ):
        raise BootstrapPlanError("plan timestamp is not whole-second UTC")
    try:
        timestamp = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise BootstrapPlanError("plan timestamp is invalid") from exc
    normalized_now = now.astimezone(UTC)
    if timestamp > normalized_now + MAXIMUM_CLOCK_SKEW:
        raise BootstrapPlanError("plan timestamp is in the future")
    cleanup = timestamp + timedelta(hours=lifetime_hours)
    if normalized_now >= cleanup:
        raise BootstrapPlanError("saved plan cleanup deadline has expired")
    return raw, cleanup.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_provider_configuration(plan: Mapping[str, Any]) -> None:
    configuration = plan.get("configuration")
    if not isinstance(configuration, Mapping):
        raise BootstrapPlanError("plan configuration is missing")
    providers = configuration.get("provider_config")
    if not isinstance(providers, Mapping) or set(providers) != {"azurerm", "terraform"}:
        raise BootstrapPlanError("plan provider set is not exact")
    azurerm = providers["azurerm"]
    builtin = providers["terraform"]
    if not isinstance(azurerm, Mapping) or not isinstance(builtin, Mapping):
        raise BootstrapPlanError("plan provider configuration is invalid")
    if (
        azurerm.get("full_name") != "registry.opentofu.org/hashicorp/azurerm"
        or azurerm.get("version_constraint") != "4.73.0"
        or builtin.get("full_name") != "terraform.io/builtin/terraform"
    ):
        raise BootstrapPlanError("plan provider source or version is not pinned")
    expressions = azurerm.get("expressions")
    expected_expressions = {
        "features": [{}],
        "resource_provider_registrations": {"constant_value": "none"},
        "subscription_id": {"references": ["local.approved_subscription_id"]},
        "tenant_id": {"references": ["local.approved_tenant_id"]},
    }
    if expressions != expected_expressions:
        raise BootstrapPlanError("AzureRM provider expressions are not exact")


def _validate_checks(plan: Mapping[str, Any]) -> None:
    checks = plan.get("checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise BootstrapPlanError("plan checks are missing")
    observed: dict[str, tuple[str, tuple[str, ...]]] = {}
    for check in checks:
        if not isinstance(check, Mapping) or not isinstance(
            check.get("address"), Mapping
        ):
            raise BootstrapPlanError("plan check is invalid")
        address = check["address"].get("to_display")
        instances = check.get("instances")
        if (
            not isinstance(address, str)
            or not isinstance(instances, Sequence)
            or isinstance(instances, (str, bytes))
            or not all(isinstance(instance, Mapping) for instance in instances)
        ):
            raise BootstrapPlanError("plan check instances are invalid")
        statuses = tuple(str(instance.get("status")) for instance in instances)
        if any(instance.get("problems") not in (None, []) for instance in instances):
            raise BootstrapPlanError("plan check reports a problem")
        if address in observed:
            raise BootstrapPlanError("plan contains a duplicate check result")
        observed[address] = (str(check.get("status")), statuses)
    expected = {
        "azurerm_user_assigned_identity.sanitizer_bootstrap": (
            "unknown",
            ("unknown",),
        ),
        "terraform_data.activation_guard": ("pass", ("pass",)),
        "var.sanitizer_bootstrap_lifetime_hours": ("pass", ("pass",)),
        "var.sanitizer_bootstrap_rehearsal_id": ("pass", ("pass",)),
    }
    if observed != expected:
        raise BootstrapPlanError("plan check result set is not exact")


def _expected_tags(*, rehearsal_id: str, cleanup: str) -> dict[str, str]:
    return {
        **EXPECTED_BASE_TAGS,
        "rehearsal-id": rehearsal_id,
        "cleanup-not-after": cleanup,
    }


def _validate_resource_changes(
    plan: Mapping[str, Any], *, rehearsal_id: str, lifetime_hours: int, cleanup: str
) -> None:
    changes = plan.get("resource_changes")
    if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes)):
        raise BootstrapPlanError("plan resource changes are missing")
    by_address: dict[str, Mapping[str, Any]] = {}
    for resource in changes:
        if not isinstance(resource, Mapping) or not isinstance(
            resource.get("address"), str
        ):
            raise BootstrapPlanError("plan contains an invalid resource change")
        if resource["address"] in by_address:
            raise BootstrapPlanError("plan contains a duplicate resource change")
        by_address[resource["address"]] = resource
    if set(by_address) != EXPECTED_CHANGE_ADDRESSES:
        raise BootstrapPlanError("plan resource change set is not exact")

    uami = by_address["azurerm_user_assigned_identity.sanitizer_bootstrap[0]"]
    guard = by_address["terraform_data.activation_guard[0]"]
    expected_headers = (
        (
            uami,
            "azurerm_user_assigned_identity",
            "sanitizer_bootstrap",
            "registry.opentofu.org/hashicorp/azurerm",
        ),
        (
            guard,
            "terraform_data",
            "activation_guard",
            "terraform.io/builtin/terraform",
        ),
    )
    for resource, resource_type, name, provider in expected_headers:
        if (
            resource.get("mode") != "managed"
            or resource.get("type") != resource_type
            or resource.get("name") != name
            or resource.get("index") != 0
            or resource.get("provider_name") != provider
        ):
            raise BootstrapPlanError("plan resource identity is not exact")
        change = resource.get("change")
        if not isinstance(change, Mapping) or change.get("actions") != ["create"]:
            raise BootstrapPlanError("plan contains a non-create resource action")
        if change.get("before") is not None:
            raise BootstrapPlanError("plan create action has pre-existing values")

    uami_change = uami["change"]
    assert isinstance(uami_change, Mapping)
    expected_uami_after = {
        "isolation_scope": None,
        "location": EXPECTED_LOCATION,
        "name": EXPECTED_IDENTITY_NAME,
        "resource_group_name": EXPECTED_RESOURCE_GROUP,
        "tags": _expected_tags(rehearsal_id=rehearsal_id, cleanup=cleanup),
        "timeouts": None,
    }
    expected_uami_unknown = {
        "client_id": True,
        "id": True,
        "principal_id": True,
        "tags": {},
        "tenant_id": True,
    }
    if (
        uami_change.get("after") != expected_uami_after
        or uami_change.get("after_unknown") != expected_uami_unknown
        or uami_change.get("before_sensitive") is not False
        or uami_change.get("after_sensitive") != {"tags": {}}
    ):
        raise BootstrapPlanError("planned UAMI values are not exact")

    lifecycle = {
        "cleanup_not_after": cleanup,
        "lifetime_hours": lifetime_hours,
        "rehearsal_id": rehearsal_id,
    }
    guard_change = guard["change"]
    assert isinstance(guard_change, Mapping)
    expected_guard_after = {
        "input": {
            "enabled": True,
            "exact_plan_approved": True,
            **lifecycle,
        },
        "triggers_replace": lifecycle,
    }
    if (
        guard_change.get("after") != expected_guard_after
        or guard_change.get("after_unknown")
        != {"id": True, "input": {}, "output": True, "triggers_replace": {}}
        or guard_change.get("before_sensitive") is not False
        or guard_change.get("after_sensitive")
        != {"input": {}, "output": {}, "triggers_replace": {}}
    ):
        raise BootstrapPlanError("planned activation guard is not exact")


def _validate_prior_state(plan: Mapping[str, Any]) -> None:
    prior = plan.get("prior_state")
    if not isinstance(prior, Mapping) or not isinstance(prior.get("values"), Mapping):
        raise BootstrapPlanError("plan prior state is missing")
    values = prior["values"]
    expected_output = {
        "state_boundary": {
            "sensitive": False,
            "value": dict(EXPECTED_STATE_BOUNDARY),
            "type": [
                "object",
                {
                    "container": "string",
                    "key": "string",
                    "resource_group": "string",
                    "storage_account": "string",
                },
            ],
        }
    }
    root = values.get("root_module")
    if values.get("outputs") != expected_output or not isinstance(root, Mapping):
        raise BootstrapPlanError("plan prior-state boundary is not exact")
    resources = root.get("resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise BootstrapPlanError("plan prior-state resources are invalid")
    if len(resources) != 1 or not isinstance(resources[0], Mapping):
        raise BootstrapPlanError("plan prior state contains an unexpected resource")
    resource = resources[0]
    expected_id = (
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
        f"{EXPECTED_RESOURCE_GROUP}"
    )
    values = resource.get("values")
    if (
        resource.get("address") != "data.azurerm_resource_group.control[0]"
        or resource.get("mode") != "data"
        or resource.get("provider_name") != "registry.opentofu.org/hashicorp/azurerm"
        or not isinstance(values, Mapping)
        or str(values.get("id", "")).lower() != expected_id.lower()
        or values.get("name") != EXPECTED_RESOURCE_GROUP
        or str(values.get("location", "")).lower() != EXPECTED_LOCATION
    ):
        raise BootstrapPlanError("plan prior-state resource group is not exact")


def _validate_outputs(
    plan: Mapping[str, Any], *, rehearsal_id: str, cleanup: str
) -> None:
    outputs = plan.get("output_changes")
    if not isinstance(outputs, Mapping) or set(outputs) != {
        "sanitizer_bootstrap_identity_foundation",
        "state_boundary",
    }:
        raise BootstrapPlanError("plan output change set is not exact")
    foundation = outputs["sanitizer_bootstrap_identity_foundation"]
    boundary = outputs["state_boundary"]
    if not isinstance(foundation, Mapping) or not isinstance(boundary, Mapping):
        raise BootstrapPlanError("plan output changes are invalid")
    expected_foundation_after = {
        "attached_job_resource_ids": [],
        "cleanup_not_after": cleanup,
        "database_authority_bound": False,
        "destruction_protected": True,
        "exact_ids_authenticated": False,
        "federated_credential_ids": [],
        "lifecycle": "ephemeral",
        "name": EXPECTED_IDENTITY_NAME,
        "provider_authority_bound": False,
        "rehearsal_id": rehearsal_id,
        "role_assignment_ids": [],
        "single_use_job_bound": False,
        "workflow_binding_implemented": False,
    }
    expected_foundation_unknown = {
        "attached_job_resource_ids": [],
        "client_id": True,
        "federated_credential_ids": [],
        "principal_id": True,
        "resource_id": True,
        "role_assignment_ids": [],
        "tenant_id": True,
    }
    if (
        foundation.get("actions") != ["create"]
        or foundation.get("before") is not None
        or foundation.get("after") != expected_foundation_after
        or foundation.get("after_unknown") != expected_foundation_unknown
        or foundation.get("before_sensitive") is not False
        or foundation.get("after_sensitive") is not False
    ):
        raise BootstrapPlanError("bootstrap identity output is not exact")
    if (
        boundary.get("actions") != ["create"]
        or boundary.get("before") is not None
        or boundary.get("after") != EXPECTED_STATE_BOUNDARY
        or boundary.get("after_unknown") is not False
        or boundary.get("before_sensitive") is not False
        or boundary.get("after_sensitive") is not False
    ):
        raise BootstrapPlanError("state-boundary output is not exact")


def _validate_configuration(plan: Mapping[str, Any]) -> None:
    configuration = plan.get("configuration")
    assert isinstance(configuration, Mapping)
    root = configuration.get("root_module")
    if not isinstance(root, Mapping):
        raise BootstrapPlanError("plan root configuration is missing")
    resources = root.get("resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise BootstrapPlanError("plan root configuration resources are invalid")
    observed = {
        resource.get("address")
        for resource in resources
        if isinstance(resource, Mapping)
    }
    if (
        observed
        != {
            "azurerm_user_assigned_identity.sanitizer_bootstrap",
            "terraform_data.activation_guard",
            "data.azurerm_resource_group.control",
        }
        or len(resources) != 3
    ):
        raise BootstrapPlanError("plan root configuration resource set is not exact")
    variables = root.get("variables")
    outputs = root.get("outputs")
    if not isinstance(variables, Mapping) or set(variables) != EXPECTED_VARIABLES:
        raise BootstrapPlanError("plan root variable declaration set is not exact")
    if not isinstance(outputs, Mapping) or set(outputs) != {
        "sanitizer_bootstrap_identity_foundation",
        "state_boundary",
    }:
        raise BootstrapPlanError("plan root output declaration set is not exact")


def _validate_plan(
    plan: Mapping[str, Any], *, now: datetime
) -> tuple[str, str, str, int]:
    if (
        plan.get("format_version") != EXPECTED_FORMAT_VERSION
        or plan.get("terraform_version") != EXPECTED_TOFU_VERSION
        or plan.get("applyable") is not True
        or plan.get("complete") is not True
        or plan.get("errored") is not False
    ):
        raise BootstrapPlanError(
            "plan format, version, completeness, or success status is invalid"
        )
    for name in ("resource_drift", "deferred_changes", "action_invocations"):
        if plan.get(name) not in (None, []):
            raise BootstrapPlanError(f"plan contains unexpected {name}")
    rehearsal_id, lifetime_hours = _plan_inputs(plan)
    plan_timestamp, cleanup = _plan_times(plan, lifetime_hours=lifetime_hours, now=now)
    _validate_provider_configuration(plan)
    _validate_configuration(plan)
    _validate_checks(plan)
    _validate_prior_state(plan)
    _validate_resource_changes(
        plan,
        rehearsal_id=rehearsal_id,
        lifetime_hours=lifetime_hours,
        cleanup=cleanup,
    )
    _validate_outputs(plan, rehearsal_id=rehearsal_id, cleanup=cleanup)
    if plan.get("resource_drift") not in (None, []):
        raise BootstrapPlanError("plan reports resource drift")
    return plan_timestamp, cleanup, rehearsal_id, lifetime_hours


def _validate_artifacts(
    *,
    saved_plan_bytes: bytes,
    expected_saved_plan_sha256: str,
    source: SourceSnapshot,
    plan_json_bytes: bytes,
    expected_state_lineage: str,
    expected_state_serial: int,
    tofu_sha256: str,
    provider_sha256: str,
    now: datetime,
) -> Mapping[str, Any]:
    saved_plan_sha256 = _sha256_bytes(saved_plan_bytes)
    if saved_plan_sha256 != expected_saved_plan_sha256:
        raise BootstrapPlanError("saved plan digest does not match reviewed evidence")
    previous_bytes, previous, refreshed_bytes, refreshed = _inspect_plan_archive(
        saved_plan_bytes, source=source
    )
    _validate_embedded_states(
        previous,
        refreshed,
        expected_lineage=expected_state_lineage,
        expected_serial=expected_state_serial,
    )
    plan = _load_json_bytes(plan_json_bytes, label="derived plan JSON")
    plan_timestamp, cleanup, rehearsal_id, lifetime_hours = _validate_plan(
        plan, now=now
    )
    return {
        "schema_version": 1,
        "policy_id": "cosmaneura-staging-sanitizer-bootstrap-identity-plan-v1",
        "source_commit": source.commit,
        "source_tree_sha256": source.tree_sha256,
        "provider_lock_sha256": _sha256_bytes(source.lockfile),
        "open_tofu_version": EXPECTED_TOFU_VERSION,
        "open_tofu_sha256": tofu_sha256,
        "open_tofu_provider_sha256": provider_sha256,
        "saved_plan_sha256": saved_plan_sha256,
        "derived_plan_json_sha256": _sha256_bytes(plan_json_bytes),
        "embedded_prior_state_sha256": _sha256_bytes(previous_bytes),
        "embedded_refreshed_state_sha256": _sha256_bytes(refreshed_bytes),
        "state_lineage": expected_state_lineage,
        "state_serial": expected_state_serial,
        "plan_timestamp": plan_timestamp,
        "cleanup_not_after": cleanup,
        "lifetime_hours": lifetime_hours,
        "rehearsal_id": rehearsal_id,
        "create_count": 2,
        "azure_create_count": 1,
        "update_count": 0,
        "destroy_or_replace_count": 0,
        "source_archive_bound": True,
        "plan_artifact_bound": True,
        "live_remote_master_bound": False,
        "live_backend_readback_bound": False,
        "live_resource_readback_bound": False,
        "apply_authorized": False,
        "pass": True,
    }


def validate_saved_plan(
    *,
    saved_plan: Path,
    expected_saved_plan_sha256: str,
    expected_source_commit: str,
    expected_state_lineage: str,
    expected_state_serial: int,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_saved_plan_sha256) is None:
        raise BootstrapPlanError("expected saved-plan digest is invalid")
    if (
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            expected_state_lineage,
        )
        is None
    ):
        raise BootstrapPlanError("expected state lineage is invalid")
    if type(expected_state_serial) is not int or expected_state_serial < 1:
        raise BootstrapPlanError("expected state serial must be a positive integer")
    saved_plan_bytes = _read_protected_file(saved_plan, label="saved plan")
    source_before = _source_snapshot(expected_source_commit)
    tofu = _resolve_and_verify_tofu()
    tofu_sha256 = _executable_sha256(tofu, label="OpenTofu")
    with tempfile.TemporaryDirectory(
        prefix=".cosmaneura-bootstrap-plan-decoder-", dir=saved_plan.parent
    ) as temporary_directory:
        data_dir = Path(temporary_directory)
        provider_sha256 = _initialize_decoder(tofu=tofu, data_dir=data_dir)
        plan_json_bytes = _show_saved_plan(
            saved_plan_bytes, tofu=tofu, data_dir=data_dir
        )
        if (data_dir / "terraform.tfstate").exists():
            raise BootstrapPlanError(
                "offline decoder unexpectedly initialized a backend"
            )
        if _provider_tree_sha256(data_dir) != provider_sha256:
            raise BootstrapPlanError(
                "offline decoder provider changed during validation"
            )
    receipt = _validate_artifacts(
        saved_plan_bytes=saved_plan_bytes,
        expected_saved_plan_sha256=expected_saved_plan_sha256,
        source=source_before,
        plan_json_bytes=plan_json_bytes,
        expected_state_lineage=expected_state_lineage,
        expected_state_serial=expected_state_serial,
        tofu_sha256=tofu_sha256,
        provider_sha256=provider_sha256,
        now=now or datetime.now(UTC),
    )
    if _source_snapshot(expected_source_commit) != source_before:
        raise BootstrapPlanError("bootstrap plan source changed during validation")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saved-plan", required=True, type=Path)
    parser.add_argument("--expected-saved-plan-sha256", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-state-lineage", required=True)
    parser.add_argument("--expected-state-serial", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = validate_saved_plan(
            saved_plan=args.saved_plan,
            expected_saved_plan_sha256=args.expected_saved_plan_sha256,
            expected_source_commit=args.expected_source_commit,
            expected_state_lineage=args.expected_state_lineage,
            expected_state_serial=args.expected_state_serial,
        )
    except BootstrapPlanError as exc:
        print(f"bootstrap_identity_plan_refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
