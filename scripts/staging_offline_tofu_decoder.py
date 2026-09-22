"""Run only OpenTofu version/show inside a private, network-denied runtime."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts import staging_offline_plan_runtime as offline_runtime
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    import staging_offline_plan_runtime as offline_runtime

MAXIMUM_PLAN_BYTES = 128 * 1024 * 1024
MAXIMUM_PLAN_JSON_BYTES = 16 * 1024 * 1024
SYSTEM_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
SYSTEM_TEMPORARY_ROOT = Path("/private/tmp")
AZURERM_PROVIDER_SOURCE = "registry.opentofu.org/hashicorp/azurerm"
SUPPORTED_PLAN_FORMAT_VERSION = "1.2"
PROVIDER_EXECUTABLE_NAME = "terraform-provider-azurerm"
EXPECTED_PROVIDER_PACKAGE_FILES = frozenset(
    {
        "CHANGELOG-v0.md",
        "CHANGELOG-v1.md",
        "CHANGELOG-v2.md",
        "CHANGELOG-v3.md",
        "CHANGELOG.md",
        "LICENSE",
        "README.md",
        PROVIDER_EXECUTABLE_NAME,
    }
)

OfflineTofuDecoderError = offline_runtime.OfflineRuntimeError


@dataclass(frozen=True)
class DecodedPlan:
    plan_json: bytes
    tofu_sha256: str
    provider_sha256: str
    provider_package_h1: str


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _collect_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    maximum_output_bytes: int,
) -> tuple[int, bytes, bytes]:
    stdout = None
    stderr = None
    selector: selectors.BaseSelector | None = None
    deadline = time.monotonic() + timeout_seconds
    try:
        stdout = process.stdout
        stderr = process.stderr
        if stdout is None or stderr is None:
            raise OfflineTofuDecoderError("offline decoder pipes are unavailable")
        streams: dict[int, list[bytes]] = {stdout.fileno(): [], stderr.fileno(): []}
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ, stdout.fileno())
        selector.register(stderr, selectors.EVENT_READ, stderr.fileno())
        total = 0
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OfflineTofuDecoderError("offline decoder timed out")
            for key, _ in selector.select(min(remaining, 0.5)):
                chunk = os.read(
                    key.fd, min(64 * 1024, maximum_output_bytes - total + 1)
                )
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > maximum_output_bytes:
                    raise OfflineTofuDecoderError(
                        "offline decoder output exceeded its limit"
                    )
                streams[key.data].append(chunk)
        while True:
            if time.monotonic() >= deadline:
                raise OfflineTofuDecoderError("offline decoder timed out")
            status = os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
            if status is not None:
                break
            time.sleep(0.01)
        captured_stdout = b"".join(streams[stdout.fileno()])
        captured_stderr = b"".join(streams[stderr.fileno()])
    except (OSError, subprocess.TimeoutExpired, OfflineTofuDecoderError) as exc:
        if isinstance(exc, OfflineTofuDecoderError):
            raise
        raise OfflineTofuDecoderError("offline decoder did not complete") from exc
    finally:
        _terminate_process(process)
        if selector is not None:
            selector.close()
        if stdout is not None:
            stdout.close()
        if stderr is not None:
            stderr.close()
    if process.returncode is None:
        raise OfflineTofuDecoderError("offline decoder process could not be reaped")
    return process.returncode, captured_stdout, captured_stderr


def _trusted_system_executable(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise OfflineTofuDecoderError(f"the {label} executable is unavailable") from exc
    if (
        resolved != path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise OfflineTofuDecoderError(f"the {label} executable is not trusted")
    return resolved


def _trusted_temporary_root() -> Path:
    try:
        resolved = SYSTEM_TEMPORARY_ROOT.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise OfflineTofuDecoderError("system temporary root is unavailable") from exc
    if (
        resolved != SYSTEM_TEMPORARY_ROOT
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or not metadata.st_mode & stat.S_ISVTX
        or stat.S_IMODE(metadata.st_mode) & 0o022 != 0o022
    ):
        raise OfflineTofuDecoderError("system temporary root is not trusted")
    return resolved


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validate_provider_package_ancestry(package: Path) -> None:
    offline_runtime.reject_symlink_components(package, label="provider package")
    chain = (package, *package.parents)
    previous: os.stat_result | None = None
    for path in chain:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise OfflineTofuDecoderError(
                "provider package ancestry is unavailable"
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {
            0,
            os.geteuid(),
        }:
            raise OfflineTofuDecoderError("provider package ancestry is not trusted")
        if mode & 0o022:
            if not (
                metadata.st_uid == 0
                and metadata.st_mode & stat.S_ISVTX
                and previous is not None
                and previous.st_uid == os.geteuid()
                and stat.S_IMODE(previous.st_mode) == 0o700
            ):
                raise OfflineTofuDecoderError(
                    "provider package ancestry is not trusted"
                )
        previous = metadata


def _open_provider_member(
    package_descriptor: int,
    name: str,
    *,
    executable: bool,
) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=package_descriptor,
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise OfflineTofuDecoderError(
            "provider package member could not be opened safely"
        ) from exc
    maximum_bytes = (
        offline_runtime.MAX_EXECUTABLE_BYTES
        if executable
        else offline_runtime.MAX_FILE_BYTES
    )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or executable != bool(metadata.st_mode & 0o111)
        or not 0 < metadata.st_size <= maximum_bytes
    ):
        os.close(descriptor)
        raise OfflineTofuDecoderError("provider package member is not exact")
    return descriptor, metadata


def _read_provider_member(package_descriptor: int, name: str) -> bytes:
    descriptor, before = _open_provider_member(
        package_descriptor, name, executable=False
    )
    try:
        value = bytearray()
        while len(value) < before.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, before.st_size - len(value)))
            if not chunk:
                break
            value.extend(chunk)
        after = os.fstat(descriptor)
        linked = os.stat(name, dir_fd=package_descriptor, follow_symlinks=False)
        if (
            len(value) != before.st_size
            or _file_identity(before) != _file_identity(after)
            or _file_identity(after) != _file_identity(linked)
        ):
            raise OfflineTofuDecoderError(
                "provider package member changed while it was read"
            )
        return bytes(value)
    except OSError as exc:
        raise OfflineTofuDecoderError(
            "provider package member could not be read safely"
        ) from exc
    finally:
        os.close(descriptor)


def _copy_provider_executable(
    package_descriptor: int,
    destination: Path,
    *,
    expected_sha256: str,
) -> Path:
    source, before = _open_provider_member(
        package_descriptor, PROVIDER_EXECUTABLE_NAME, executable=True
    )
    parent = offline_runtime.open_private_directory(
        destination.parent, label="private AzureRM provider directory"
    )
    target: int | None = None
    try:
        target = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o500,
            dir_fd=parent,
        )
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(source, 1024 * 1024):
            total += len(chunk)
            if total > offline_runtime.MAX_EXECUTABLE_BYTES:
                raise OfflineTofuDecoderError(
                    "AzureRM provider executable is too large"
                )
            digest.update(chunk)
            offset = 0
            while offset < len(chunk):
                offset += os.write(target, chunk[offset:])
        os.fsync(target)
        os.fchmod(target, 0o500)
        os.fsync(parent)
        after = os.fstat(source)
        linked = os.stat(
            PROVIDER_EXECUTABLE_NAME,
            dir_fd=package_descriptor,
            follow_symlinks=False,
        )
        if (
            total != before.st_size
            or _file_identity(before) != _file_identity(after)
            or _file_identity(after) != _file_identity(linked)
            or digest.hexdigest() != expected_sha256
        ):
            raise OfflineTofuDecoderError(
                "AzureRM provider executable did not match its pinned digest"
            )
    except OSError as exc:
        raise OfflineTofuDecoderError(
            "AzureRM provider executable could not be copied safely"
        ) from exc
    finally:
        if target is not None:
            os.close(target)
        os.close(parent)
        os.close(source)
    offline_runtime.verify_private_executable(
        destination, expected=expected_sha256, label="AzureRM provider"
    )
    return destination


def _stage_provider_package(
    package: Path,
    destination: Path,
    *,
    provider_version: str,
    provider_platform: str,
    expected_provider_sha256: str,
) -> tuple[Mapping[str, bytes], Path]:
    expected_suffix = (
        "registry.opentofu.org",
        "hashicorp",
        "azurerm",
        provider_version,
        provider_platform,
    )
    if (
        not package.is_absolute()
        or tuple(package.parts[-len(expected_suffix) :]) != expected_suffix
    ):
        raise OfflineTofuDecoderError("provider package path is not exact")
    _validate_provider_package_ancestry(package)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            package,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_DIRECTORY", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or frozenset(os.listdir(descriptor)) != EXPECTED_PROVIDER_PACKAGE_FILES
        ):
            raise OfflineTofuDecoderError("provider package is not exact")
        files = {
            name: _read_provider_member(descriptor, name)
            for name in sorted(
                EXPECTED_PROVIDER_PACKAGE_FILES - {PROVIDER_EXECUTABLE_NAME}
            )
        }
        offline_runtime.materialize_source(destination, files)
        provider = _copy_provider_executable(
            descriptor,
            destination / PROVIDER_EXECUTABLE_NAME,
            expected_sha256=expected_provider_sha256,
        )
        after = os.fstat(descriptor)
        linked = os.stat(package, follow_symlinks=False)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity or (
            after.st_dev,
            after.st_ino,
        ) != (linked.st_dev, linked.st_ino):
            raise OfflineTofuDecoderError("provider package changed while it was read")
        return files, provider
    except OSError as exc:
        raise OfflineTofuDecoderError(
            "provider package could not be read safely"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _create_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise OfflineTofuDecoderError(
            "private decoder directory could not be created"
        ) from exc
    offline_runtime.require_private_directory(path, label="private decoder directory")


def _create_private_path(root: Path, relative: Path) -> Path:
    current = root
    for name in relative.parts:
        current /= name
        _create_private_directory(current)
    return current


def _sandbox_profile(runtime_root: Path, *, tofu: Path, provider: Path | None) -> str:
    temporary = json.dumps(str(runtime_root / "tmp"))
    executable_paths = (tofu,) if provider is None else (tofu, provider)
    executable_rules = "".join(
        f"(literal {json.dumps(str(path))})" for path in executable_paths
    )
    writable = "".join(
        f"(allow file-write* (subpath {json.dumps(str(runtime_root / name))}))"
        for name in ("home", "tmp", "xdg-cache", "xdg-config")
    )
    return (
        "(version 1)"
        "(deny default)"
        '(import "system.sb")'
        "(deny process-exec*)"
        f"(allow process-exec {executable_rules})"
        "(allow process-fork)"
        "(deny mach*)"
        "(deny ipc*)"
        "(deny signal (target others))"
        "(deny process-info* (target others))"
        "(deny network*)"
        f"(allow network-inbound (subpath {temporary}))"
        f"(allow network-outbound (subpath {temporary}))"
        "(deny file-write*)"
        '(allow file-read-metadata (literal "/") (literal "/private") '
        '(literal "/private/tmp"))'
        f"(allow file-read* (subpath {json.dumps(str(runtime_root))}))"
        f"{writable}"
    )


def _decoder_environment(
    *, runtime_root: Path, data_dir: Path, home: Path, temporary: Path, xdg: Path
) -> Mapping[str, str]:
    return {
        "CHECKPOINT_DISABLE": "1",
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "TF_CLI_CONFIG_FILE": os.devnull,
        "TF_DATA_DIR": str(data_dir),
        "TF_IN_AUTOMATION": "1",
        "TMPDIR": str(temporary),
        "XDG_CACHE_HOME": str(runtime_root / "xdg-cache"),
        "XDG_CONFIG_HOME": str(xdg),
    }


def _run_sandboxed_tofu(
    operation: str,
    *,
    tofu: Path,
    expected_tofu_sha256: str,
    provider: Path | None,
    expected_provider_sha256: str | None,
    runtime_root: Path,
    source: Path,
    data_dir: Path,
    plan: Path,
) -> bytes:
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        raise OfflineTofuDecoderError("offline decoder requires Darwin arm64")
    if (tofu, source, data_dir, plan) != (
        runtime_root / "bin/tofu",
        runtime_root / "source",
        runtime_root / "data",
        runtime_root / "plan/plan.tfplan",
    ):
        raise OfflineTofuDecoderError("offline decoder paths are not exact")
    if (provider is None) != (expected_provider_sha256 is None):
        raise OfflineTofuDecoderError("offline decoder provider identity is incomplete")
    if provider is not None:
        try:
            provider_parts = provider.relative_to(data_dir).parts
        except ValueError as exc:
            raise OfflineTofuDecoderError(
                "offline decoder provider path is not exact"
            ) from exc
        if (
            len(provider_parts) != 7
            or provider_parts[:4]
            != ("providers", "registry.opentofu.org", "hashicorp", "azurerm")
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", provider_parts[4]) is None
            or provider_parts[5:] != ("darwin_arm64", PROVIDER_EXECUTABLE_NAME)
        ):
            raise OfflineTofuDecoderError("offline decoder provider path is not exact")
    offline_runtime.require_private_directory(
        runtime_root, label="private decoder root"
    )
    for path, label in (
        (source, "private decoder source"),
        (data_dir, "private decoder data"),
        (plan.parent, "private decoder plan"),
        (runtime_root / "home", "private decoder home"),
        (runtime_root / "tmp", "private decoder temporary directory"),
        (runtime_root / "xdg-cache", "private decoder cache"),
        (runtime_root / "xdg-config", "private decoder configuration"),
    ):
        offline_runtime.require_private_directory(path, label=label)
    offline_runtime.verify_private_executable(
        tofu, expected=expected_tofu_sha256, label="OpenTofu"
    )
    if provider is not None and expected_provider_sha256 is not None:
        offline_runtime.verify_private_executable(
            provider, expected=expected_provider_sha256, label="AzureRM provider"
        )
    sandbox = _trusted_system_executable(SYSTEM_SANDBOX_EXEC, label="sandbox-exec")
    home = runtime_root / "home"
    temporary = runtime_root / "tmp"
    xdg = runtime_root / "xdg-config"
    if operation == "version":
        tofu_arguments = ("version", "-json")
        timeout_seconds = 30
        maximum_output_bytes = 1024 * 1024
    elif operation == "show":
        tofu_arguments = (f"-chdir={source}", "show", "-json", "-no-color", str(plan))
        timeout_seconds = 120
        maximum_output_bytes = MAXIMUM_PLAN_JSON_BYTES
    else:
        raise OfflineTofuDecoderError("OpenTofu operation is not approved")
    command = (
        str(sandbox),
        "-p",
        _sandbox_profile(runtime_root, tofu=tofu, provider=provider),
        str(tofu),
        *tofu_arguments,
    )
    try:
        process = subprocess.Popen(
            command,
            cwd=runtime_root,
            env=dict(
                _decoder_environment(
                    runtime_root=runtime_root,
                    data_dir=data_dir,
                    home=home,
                    temporary=temporary,
                    xdg=xdg,
                )
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            umask=0o077,
            start_new_session=True,
        )
    except OSError as exc:
        raise OfflineTofuDecoderError("the offline decoder did not start") from exc
    returncode, stdout, _stderr = _collect_process(
        process,
        timeout_seconds=timeout_seconds,
        maximum_output_bytes=maximum_output_bytes,
    )
    if returncode != 0:
        raise OfflineTofuDecoderError(
            f"OpenTofu {operation} failed in the offline sandbox"
        )
    if not stdout:
        raise OfflineTofuDecoderError(f"OpenTofu {operation} returned no output")
    return stdout


def _verify_staged_files(
    *,
    source_directory: Path,
    terraform_files: Mapping[str, bytes],
    plan_path: Path,
    plan_bytes: bytes,
    provider_directory: Path,
    provider_files: Mapping[str, bytes],
) -> None:
    if frozenset(os.listdir(source_directory)) != frozenset(terraform_files):
        raise OfflineTofuDecoderError("private decoder source changed")
    for name, expected in terraform_files.items():
        observed = offline_runtime.read_private_file(
            source_directory / name, label=f"private decoder source {name}"
        )
        if observed != expected:
            raise OfflineTofuDecoderError("private decoder source changed")
    if offline_runtime.read_private_file(
        plan_path, label="private decoder plan"
    ) != plan_bytes or frozenset(os.listdir(plan_path.parent)) != {"plan.tfplan"}:
        raise OfflineTofuDecoderError("private decoder plan changed")
    if frozenset(os.listdir(provider_directory)) != EXPECTED_PROVIDER_PACKAGE_FILES:
        raise OfflineTofuDecoderError("private provider package changed")
    for name, expected in provider_files.items():
        observed = offline_runtime.read_private_file(
            provider_directory / name, label=f"private provider package {name}"
        )
        if observed != expected:
            raise OfflineTofuDecoderError("private provider package changed")


def _verify_data_tree(data_directory: Path, provider_directory: Path) -> None:
    provider_relative = provider_directory.relative_to(data_directory)
    expected_directories = {
        Path(*provider_relative.parts[:length])
        for length in range(1, len(provider_relative.parts) + 1)
    }
    expected_paths = expected_directories | {
        provider_relative / name for name in EXPECTED_PROVIDER_PACKAGE_FILES
    }
    observed_paths: set[Path] = set()
    for directory, names, files in os.walk(data_directory, followlinks=False):
        directory_path = Path(directory)
        for name in (*names, *files):
            path = directory_path / name
            relative = path.relative_to(data_directory)
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise OfflineTofuDecoderError("private decoder data tree is unsafe")
            observed_paths.add(relative)
    if observed_paths != expected_paths:
        raise OfflineTofuDecoderError("private decoder data tree changed")


def _validate_policy_values(
    *,
    plan_bytes: bytes,
    expected_tofu_version: str,
    expected_tofu_sha256: str,
    provider_version: str,
    provider_platform: str,
    expected_provider_sha256: str,
    expected_provider_package_h1: str,
) -> None:
    if (
        not isinstance(plan_bytes, bytes)
        or not 0 < len(plan_bytes) <= MAXIMUM_PLAN_BYTES
    ):
        raise OfflineTofuDecoderError("saved plan bytes are outside the accepted range")
    for value, label in (
        (expected_tofu_sha256, "OpenTofu"),
        (expected_provider_sha256, "AzureRM provider"),
    ):
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise OfflineTofuDecoderError(f"the {label} digest is invalid")
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", expected_tofu_version) is None:
        raise OfflineTofuDecoderError("the OpenTofu version is invalid")
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", provider_version) is None:
        raise OfflineTofuDecoderError("the provider version is invalid")
    if provider_platform != "darwin_arm64":
        raise OfflineTofuDecoderError("the provider platform is not approved")
    if re.fullmatch(r"h1:[A-Za-z0-9+/]{43}=", expected_provider_package_h1) is None:
        raise OfflineTofuDecoderError("the AzureRM provider package digest is invalid")


def _validate_provider_lock(
    lockfile: bytes, *, provider_version: str, expected_provider_package_h1: str
) -> None:
    if not isinstance(lockfile, bytes) or not lockfile:
        raise OfflineTofuDecoderError("the provider lock is invalid")
    try:
        text = lockfile.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OfflineTofuDecoderError("the provider lock is invalid") from exc
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if (
        len(lines) < 7
        or lines[0] != f'provider "{AZURERM_PROVIDER_SOURCE}" {{'
        or re.fullmatch(rf'version\s*=\s*"{re.escape(provider_version)}"', lines[1])
        is None
        or re.fullmatch(rf'constraints\s*=\s*"{re.escape(provider_version)}"', lines[2])
        is None
        or lines[3] != "hashes = ["
        or lines[-2:] != ["]", "}"]
    ):
        raise OfflineTofuDecoderError("the provider lock is not exact")
    hashes: list[str] = []
    for line in lines[4:-2]:
        match = re.fullmatch(r'"(h1:[A-Za-z0-9+/]{43}=|zh:[0-9a-f]{64})",', line)
        if match is None:
            raise OfflineTofuDecoderError("the provider lock is not exact")
        hashes.append(match.group(1))
    if len(hashes) != len(set(hashes)) or expected_provider_package_h1 not in hashes:
        raise OfflineTofuDecoderError("the provider lock is not exact")


def _validate_decoded_plan_json(
    plan_json: bytes, *, expected_tofu_version: str, provider_version: str
) -> None:
    document = offline_runtime.load_json_bytes(plan_json, label="decoded OpenTofu plan")
    if (
        document.get("format_version") != SUPPORTED_PLAN_FORMAT_VERSION
        or document.get("terraform_version") != expected_tofu_version
    ):
        raise OfflineTofuDecoderError("decoded OpenTofu plan identity is not exact")
    configuration = document.get("configuration")
    if not isinstance(configuration, Mapping):
        raise OfflineTofuDecoderError("decoded OpenTofu plan has no configuration")
    providers = configuration.get("provider_config")
    if not isinstance(providers, Mapping):
        raise OfflineTofuDecoderError("decoded OpenTofu plan has no provider binding")
    matches = [
        value
        for value in providers.values()
        if isinstance(value, Mapping)
        and value.get("name") == "azurerm"
        and value.get("full_name") == AZURERM_PROVIDER_SOURCE
        and value.get("version_constraint") == provider_version
    ]
    if len(matches) != 1:
        raise OfflineTofuDecoderError(
            "decoded OpenTofu plan is not bound to the pinned AzureRM provider"
        )


def _provider_package_h1(
    provider_files: Mapping[str, bytes], *, provider_sha256: str
) -> str:
    expected_files = EXPECTED_PROVIDER_PACKAGE_FILES - {PROVIDER_EXECUTABLE_NAME}
    if frozenset(provider_files) != expected_files:
        raise OfflineTofuDecoderError("provider package member set is not exact")
    digest = hashlib.sha256()
    for name in sorted(EXPECTED_PROVIDER_PACKAGE_FILES):
        member_sha256 = (
            provider_sha256
            if name == PROVIDER_EXECUTABLE_NAME
            else offline_runtime.sha256_bytes(provider_files[name])
        )
        digest.update(f"{member_sha256}  {name}\n".encode())
    return f"h1:{base64.b64encode(digest.digest()).decode()}"


def decode_saved_plan(
    plan_bytes: bytes,
    *,
    terraform_files: Mapping[str, bytes],
    provider_package: Path,
    tofu_source: Path,
    expected_tofu_version: str,
    expected_tofu_sha256: str,
    provider_version: str,
    provider_platform: str,
    expected_provider_sha256: str,
    expected_provider_package_h1: str,
) -> DecodedPlan:
    """Decode one captured plan without init, network, backend, plan, or apply."""
    _validate_policy_values(
        plan_bytes=plan_bytes,
        expected_tofu_version=expected_tofu_version,
        expected_tofu_sha256=expected_tofu_sha256,
        provider_version=provider_version,
        provider_platform=provider_platform,
        expected_provider_sha256=expected_provider_sha256,
        expected_provider_package_h1=expected_provider_package_h1,
    )
    runtime_parent = _trusted_temporary_root()
    try:
        source_files = dict(terraform_files)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise OfflineTofuDecoderError(
            "decoder source members could not be read"
        ) from exc
    source_names = frozenset(source_files)
    if (
        ".terraform.lock.hcl" not in source_names
        or not any(name.endswith(".tf") for name in source_names)
        or any(
            name != ".terraform.lock.hcl" and not name.endswith(".tf")
            for name in source_names
        )
    ):
        raise OfflineTofuDecoderError("decoder source file set is not exact")
    _validate_provider_lock(
        source_files[".terraform.lock.hcl"],
        provider_version=provider_version,
        expected_provider_package_h1=expected_provider_package_h1,
    )
    with tempfile.TemporaryDirectory(
        prefix=".cn-otf-", dir=runtime_parent
    ) as temporary_directory:
        runtime_root = Path(temporary_directory)
        runtime_root.chmod(0o700)
        offline_runtime.require_private_directory(
            runtime_root, label="private decoder root"
        )
        bin_directory = _create_private_path(runtime_root, Path("bin"))
        data_directory = _create_private_path(runtime_root, Path("data"))
        for name in ("home", "tmp", "xdg-cache", "xdg-config"):
            _create_private_path(runtime_root, Path(name))
        _create_private_directory(runtime_root / "xdg-config/opentofu")
        source_directory = runtime_root / "source"
        offline_runtime.materialize_source(source_directory, source_files)
        plan_directory = runtime_root / "plan"
        offline_runtime.materialize_source(plan_directory, {"plan.tfplan": plan_bytes})
        tofu = offline_runtime.copy_pinned_executable(
            tofu_source,
            bin_directory / "tofu",
            expected=expected_tofu_sha256,
            label="OpenTofu",
        )
        provider_parent = _create_private_path(
            data_directory,
            Path(
                "providers/registry.opentofu.org/hashicorp/azurerm",
                provider_version,
            ),
        )
        provider_directory = provider_parent / provider_platform
        provider_files, provider = _stage_provider_package(
            provider_package,
            provider_directory,
            provider_version=provider_version,
            provider_platform=provider_platform,
            expected_provider_sha256=expected_provider_sha256,
        )
        if (
            _provider_package_h1(
                provider_files, provider_sha256=expected_provider_sha256
            )
            != expected_provider_package_h1
        ):
            raise OfflineTofuDecoderError(
                "AzureRM provider package did not match its pinned digest"
            )
        plan_path = plan_directory / "plan.tfplan"
        _verify_staged_files(
            source_directory=source_directory,
            terraform_files=source_files,
            plan_path=plan_path,
            plan_bytes=plan_bytes,
            provider_directory=provider_directory,
            provider_files=provider_files,
        )
        _verify_data_tree(data_directory, provider_directory)
        tofu_sha256 = offline_runtime.verify_private_executable(
            tofu, expected=expected_tofu_sha256, label="OpenTofu"
        )
        provider_sha256 = offline_runtime.verify_private_executable(
            provider, expected=expected_provider_sha256, label="AzureRM provider"
        )
        version_bytes = _run_sandboxed_tofu(
            "version",
            tofu=tofu,
            expected_tofu_sha256=expected_tofu_sha256,
            provider=provider,
            expected_provider_sha256=expected_provider_sha256,
            runtime_root=runtime_root,
            source=source_directory,
            data_dir=data_directory,
            plan=plan_path,
        )
        version = offline_runtime.load_json_bytes(
            version_bytes, label="OpenTofu version output"
        )
        if version.get("terraform_version") != expected_tofu_version:
            raise OfflineTofuDecoderError(
                "the OpenTofu executable version is not pinned"
            )
        offline_runtime.verify_private_executable(
            tofu, expected=expected_tofu_sha256, label="OpenTofu"
        )
        offline_runtime.verify_private_executable(
            provider, expected=expected_provider_sha256, label="AzureRM provider"
        )
        plan_json = _run_sandboxed_tofu(
            "show",
            tofu=tofu,
            expected_tofu_sha256=expected_tofu_sha256,
            provider=provider,
            expected_provider_sha256=expected_provider_sha256,
            runtime_root=runtime_root,
            source=source_directory,
            data_dir=data_directory,
            plan=plan_path,
        )
        _validate_decoded_plan_json(
            plan_json,
            expected_tofu_version=expected_tofu_version,
            provider_version=provider_version,
        )
        _verify_staged_files(
            source_directory=source_directory,
            terraform_files=source_files,
            plan_path=plan_path,
            plan_bytes=plan_bytes,
            provider_directory=provider_directory,
            provider_files=provider_files,
        )
        _verify_data_tree(data_directory, provider_directory)
        offline_runtime.verify_private_executable(
            tofu, expected=expected_tofu_sha256, label="OpenTofu"
        )
        offline_runtime.verify_private_executable(
            provider, expected=expected_provider_sha256, label="AzureRM provider"
        )
        if any(runtime_root.rglob("terraform.tfstate")):
            raise OfflineTofuDecoderError(
                "offline decoder unexpectedly created backend state"
            )
    if len(plan_json) > MAXIMUM_PLAN_JSON_BYTES:
        raise OfflineTofuDecoderError(
            "derived plan JSON size is outside the accepted range"
        )
    return DecodedPlan(
        plan_json=plan_json,
        tofu_sha256=tofu_sha256,
        provider_sha256=provider_sha256,
        provider_package_h1=expected_provider_package_h1,
    )
