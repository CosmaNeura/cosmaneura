#!/usr/bin/env python3
"""Validate an exact sanitizer-bootstrap identity plan without authorizing apply."""

from __future__ import annotations

import argparse
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
import time
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from scripts import staging_offline_plan_runtime as offline_runtime
    from scripts import staging_offline_tofu_decoder as offline_tofu_decoder
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    import staging_offline_plan_runtime as offline_runtime
    import staging_offline_tofu_decoder as offline_tofu_decoder

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_ROOT = REPOSITORY_ROOT / "infra-staging-sanitizer-bootstrap-identity"
VALIDATOR_PATH = Path(__file__).resolve()
RUNTIME_PATH = REPOSITORY_ROOT / "scripts/staging_offline_plan_runtime.py"
DECODER_PATH = REPOSITORY_ROOT / "scripts/staging_offline_tofu_decoder.py"

EXPECTED_FORMAT_VERSION = "1.2"
EXPECTED_TOFU_VERSION = "1.12.5"
EXPECTED_TOFU_SHA256 = (
    "1334ccf2360fe38460c47fd0924555f411cf714813e541d17d744283c973aa99"
)
EXPECTED_PROVIDER_PLATFORM = "darwin_arm64"
EXPECTED_PROVIDER_SHA256 = (
    "c983dc4efe57ed9a15162c5b1b6438e597c7f34706498ab6cc6d07b40e854e06"
)
EXPECTED_PROVIDER_PACKAGE_H1 = "h1:/GrU387kLaubwGZboawLjs4j5FDuVTcR2brU3ONOYxQ="
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
EXPECTED_CONTROL_GROUP_TAGS: Mapping[str, str] = {
    "data-classification": "non-phi-control-metadata",
    "environment": "staging",
    "managed-by": "opentofu",
    "purpose": "staging-restore-rehearsal-control",
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
MAXIMUM_PLAN_ARCHIVE_BYTES = 128 * 1024 * 1024
MAXIMUM_CLOCK_SKEW = timedelta(minutes=5)
SYSTEM_GIT = Path("/Applications/Xcode.app/Contents/Developer/usr/bin/git")
SYSTEM_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
BootstrapPlanError = offline_runtime.OfflineRuntimeError
_canonical_file_set_sha256 = offline_runtime.canonical_file_set_sha256
_load_json_bytes = offline_runtime.load_json_bytes
_read_checkout_file = offline_runtime.read_checkout_file
_read_protected_file = offline_runtime.read_private_file
_reject_nonfinite_json = offline_runtime.reject_nonfinite_json
_sha256_bytes = offline_runtime.sha256_bytes
_unique_json_object = offline_runtime.unique_json_object


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SourceSnapshot:
    commit: str
    terraform_files: Mapping[str, bytes]
    lockfile: bytes
    tree_sha256: str


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
            raise BootstrapPlanError("required local command pipes are unavailable")
        streams: dict[int, list[bytes]] = {stdout.fileno(): [], stderr.fileno(): []}
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ, stdout.fileno())
        selector.register(stderr, selectors.EVENT_READ, stderr.fileno())
        total = 0
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
                if total > maximum_output_bytes:
                    raise BootstrapPlanError(
                        "required local command output exceeded limit"
                    )
                streams[key.data].append(chunk)
        while True:
            if time.monotonic() >= deadline:
                raise BootstrapPlanError("required local command timed out")
            status = os.waitid(
                os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
            )
            if status is not None:
                break
            time.sleep(0.01)
        captured_stdout = b"".join(streams[stdout.fileno()])
        captured_stderr = b"".join(streams[stderr.fileno()])
    except (OSError, subprocess.TimeoutExpired, BootstrapPlanError) as exc:
        if isinstance(exc, BootstrapPlanError):
            raise
        raise BootstrapPlanError("required local command did not complete") from exc
    finally:
        _terminate_process(process)
        if selector is not None:
            selector.close()
        if stdout is not None:
            stdout.close()
        if stderr is not None:
            stderr.close()
    if process.returncode is None:
        raise BootstrapPlanError("required local command could not be reaped")
    return process.returncode, captured_stdout, captured_stderr


def _trusted_system_executable(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise BootstrapPlanError(f"the {label} executable is unavailable") from exc
    if (
        resolved != path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise BootstrapPlanError(f"the {label} executable is not trusted")
    return resolved


def _git_arguments_are_read_only(arguments: tuple[str, ...]) -> bool:
    source_root = str(IDENTITY_ROOT.relative_to(REPOSITORY_ROOT))
    support_paths = (
        str(VALIDATOR_PATH.relative_to(REPOSITORY_ROOT)),
        str(RUNTIME_PATH.relative_to(REPOSITORY_ROOT)),
        str(DECODER_PATH.relative_to(REPOSITORY_ROOT)),
    )
    fixed = {
        ("rev-parse", "HEAD"),
        ("rev-parse", "refs/remotes/origin/master"),
        ("remote", "get-url", "--all", "origin"),
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            source_root,
            *support_paths,
        ),
    }
    if arguments in fixed:
        return True
    if (
        len(arguments) == 6
        and arguments[:3] == ("ls-tree", "-r", "--name-only")
        and re.fullmatch(r"[0-9a-f]{40}", arguments[3]) is not None
        and arguments[4:] == ("--", source_root)
    ):
        return True
    if len(arguments) != 2 or arguments[0] != "show":
        return False
    commit, separator, name = arguments[1].partition(":")
    if separator != ":" or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    return name in support_paths or (
        name.startswith(f"{source_root}/")
        and "/" not in name.removeprefix(f"{source_root}/")
        and (name.endswith(".tf") or name.endswith("/.terraform.lock.hcl"))
    )


def _git_sandbox_profile(git: Path) -> str:
    return (
        '(version 1)(deny default)(import "system.sb")(deny process-exec*)'
        f"(allow process-exec (literal {json.dumps(str(git))}))"
        "(allow process-fork)(deny mach*)(deny ipc*)"
        "(deny signal (target others))(deny process-info* (target others))"
        "(deny network*)(deny file-write*)(allow file-read*)"
    )


def _run_git(arguments: Sequence[str]) -> bytes:
    exact_arguments = tuple(arguments)
    if not _git_arguments_are_read_only(exact_arguments):
        raise BootstrapPlanError("the Git operation is not an approved read")
    git = _trusted_system_executable(SYSTEM_GIT, label="Git")
    sandbox = _trusted_system_executable(SYSTEM_SANDBOX_EXEC, label="sandbox-exec")
    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
    }
    try:
        process = subprocess.Popen(
            (
                str(sandbox),
                "-p",
                _git_sandbox_profile(git),
                str(git),
                "--no-optional-locks",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.untrackedCache=false",
                "-C",
                str(REPOSITORY_ROOT),
                *exact_arguments,
            ),
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            umask=0o077,
            start_new_session=True,
        )
    except OSError as exc:
        raise BootstrapPlanError("the source checkout could not be verified") from exc
    returncode, stdout, _stderr = _collect_process(
        process,
        timeout_seconds=30,
        maximum_output_bytes=MAXIMUM_ARTIFACT_BYTES,
    )
    if returncode != 0:
        raise BootstrapPlanError("the source checkout could not be verified")
    return stdout


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
        str(RUNTIME_PATH.relative_to(REPOSITORY_ROOT)),
        str(DECODER_PATH.relative_to(REPOSITORY_ROOT)),
    )
    if _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all", "--", *relevant_paths]
    ):
        raise BootstrapPlanError("bootstrap plan source paths are not clean")

    source_root = IDENTITY_ROOT.relative_to(REPOSITORY_ROOT)
    expected_names = set(
        _run_git(
            ["ls-tree", "-r", "--name-only", expected_commit, "--", str(source_root)]
        )
        .decode("utf-8")
        .splitlines()
    )
    selected = sorted(
        name
        for name in expected_names
        if Path(name).parent == source_root
        and (name.endswith(".tf") or Path(name).name == ".terraform.lock.hcl")
    )
    if not selected or str(source_root / ".terraform.lock.hcl") not in selected:
        raise BootstrapPlanError("bootstrap source file set is incomplete")
    committed = {
        Path(name).name: _run_git(["show", f"{expected_commit}:{name}"])
        for name in selected
    }
    for name, value in committed.items():
        if (
            _read_checkout_file(IDENTITY_ROOT / name, label=f"bootstrap source {name}")
            != value
        ):
            raise BootstrapPlanError("bootstrap source differs from the commit")
    support_files: dict[str, bytes] = {}
    for support_path, label in (
        (VALIDATOR_PATH, "bootstrap plan validator"),
        (RUNTIME_PATH, "offline plan runtime"),
        (DECODER_PATH, "offline OpenTofu decoder"),
    ):
        name = str(support_path.relative_to(REPOSITORY_ROOT))
        committed_value = _run_git(["show", f"{expected_commit}:{name}"])
        if _read_checkout_file(support_path, label=label) != committed_value:
            raise BootstrapPlanError(f"{label} differs from the commit")
        support_files[name] = committed_value
    lockfile = committed.pop(".terraform.lock.hcl")
    tree_files = {
        **committed,
        ".terraform.lock.hcl": lockfile,
        **support_files,
    }
    return SourceSnapshot(
        commit=head,
        terraform_files=committed,
        lockfile=lockfile,
        tree_sha256=_canonical_file_set_sha256(tree_files),
    )


def _decode_saved_plan(
    plan_bytes: bytes,
    *,
    source: SourceSnapshot,
    provider_package: Path,
) -> offline_tofu_decoder.DecodedPlan:
    tofu_source = shutil.which("tofu")
    if tofu_source is None:
        raise BootstrapPlanError("the pinned OpenTofu executable is unavailable")
    return offline_tofu_decoder.decode_saved_plan(
        plan_bytes,
        terraform_files={
            **source.terraform_files,
            ".terraform.lock.hcl": source.lockfile,
        },
        provider_package=provider_package,
        tofu_source=Path(tofu_source),
        expected_tofu_version=EXPECTED_TOFU_VERSION,
        expected_tofu_sha256=EXPECTED_TOFU_SHA256,
        provider_version="4.73.0",
        provider_platform=EXPECTED_PROVIDER_PLATFORM,
        expected_provider_sha256=EXPECTED_PROVIDER_SHA256,
        expected_provider_package_h1=EXPECTED_PROVIDER_PACKAGE_H1,
    )


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
    if set(resource) != {*expected_fields, "instances"} or any(
        resource.get(name) != value for name, value in expected_fields.items()
    ):
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
        set(instance)
        != {"index_key", "schema_version", "attributes", "sensitive_attributes"}
        or instance.get("index_key") != 0
        or instance.get("schema_version") != 0
        or not isinstance(attributes, Mapping)
        or set(attributes)
        != {"id", "location", "managed_by", "name", "tags", "timeouts"}
        or str(attributes.get("id", "")).lower() != expected_id.lower()
        or attributes.get("name") != EXPECTED_RESOURCE_GROUP
        or str(attributes.get("location", "")).lower() != EXPECTED_LOCATION
        or attributes.get("managed_by") != ""
        or attributes.get("tags") != EXPECTED_CONTROL_GROUP_TAGS
        or attributes.get("timeouts") is not None
        or instance.get("sensitive_attributes") != []
    ):
        raise BootstrapPlanError("refreshed resource-group attributes are not exact")


def _validate_embedded_states(
    previous: Mapping[str, Any],
    refreshed: Mapping[str, Any],
    *,
    expected_lineage: str,
    expected_serial: int,
) -> None:
    # OpenTofu 1.12.5 writes tfstate-prev as its canonical empty sentinel for
    # a first plan against an empty remote backend. The refreshed tfstate
    # carries the authenticated backend identity; it and the decoded plan's
    # prior_state carry the exact refreshed content. Keep both archive shapes
    # closed rather than projecting the remote identity onto the sentinel.
    _state_header(previous, expected_lineage="", expected_serial=0)
    _state_header(
        refreshed,
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
    if (
        not isinstance(prior, Mapping)
        or set(prior) != {"format_version", "terraform_version", "values"}
        or prior.get("format_version") != "1.0"
        or prior.get("terraform_version") != EXPECTED_TOFU_VERSION
        or not isinstance(prior.get("values"), Mapping)
    ):
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
    if (
        set(values) != {"outputs", "root_module"}
        or values.get("outputs") != expected_output
        or not isinstance(root, Mapping)
        or set(root) != {"resources"}
    ):
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
        set(resource)
        != {
            "address",
            "mode",
            "type",
            "name",
            "index",
            "provider_name",
            "schema_version",
            "values",
            "sensitive_values",
        }
        or resource.get("address") != "data.azurerm_resource_group.control[0]"
        or resource.get("mode") != "data"
        or resource.get("type") != "azurerm_resource_group"
        or resource.get("name") != "control"
        or resource.get("index") != 0
        or resource.get("provider_name") != "registry.opentofu.org/hashicorp/azurerm"
        or resource.get("schema_version") != 0
        or not isinstance(values, Mapping)
        or set(values) != {"id", "location", "managed_by", "name", "tags", "timeouts"}
        or str(values.get("id", "")).lower() != expected_id.lower()
        or values.get("name") != EXPECTED_RESOURCE_GROUP
        or str(values.get("location", "")).lower() != EXPECTED_LOCATION
        or values.get("managed_by") != ""
        or values.get("tags") != EXPECTED_CONTROL_GROUP_TAGS
        or values.get("timeouts") is not None
        or resource.get("sensitive_values") != {"tags": {}}
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
        or plan.get("errored") is not False
    ):
        raise BootstrapPlanError("plan format, version, or success status is invalid")
    for optional_field in ("applyable", "complete"):
        if optional_field in plan and plan[optional_field] is not True:
            raise BootstrapPlanError(f"plan reports {optional_field}=false")
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
    provider_package_h1: str,
    now: datetime,
) -> Mapping[str, Any]:
    archive = _validate_archive(
        saved_plan_bytes=saved_plan_bytes,
        expected_saved_plan_sha256=expected_saved_plan_sha256,
        source=source,
        expected_state_lineage=expected_state_lineage,
        expected_state_serial=expected_state_serial,
    )
    return _build_receipt(
        saved_plan_bytes=saved_plan_bytes,
        source=source,
        archive=archive,
        plan_json_bytes=plan_json_bytes,
        expected_state_lineage=expected_state_lineage,
        expected_state_serial=expected_state_serial,
        tofu_sha256=tofu_sha256,
        provider_sha256=provider_sha256,
        provider_package_h1=provider_package_h1,
        now=now,
    )


def _validate_archive(
    *,
    saved_plan_bytes: bytes,
    expected_saved_plan_sha256: str,
    source: SourceSnapshot,
    expected_state_lineage: str,
    expected_state_serial: int,
) -> tuple[bytes, Mapping[str, Any], bytes, Mapping[str, Any]]:
    saved_plan_sha256 = _sha256_bytes(saved_plan_bytes)
    if saved_plan_sha256 != expected_saved_plan_sha256:
        raise BootstrapPlanError("saved plan digest does not match reviewed evidence")
    archive = _inspect_plan_archive(saved_plan_bytes, source=source)
    previous_bytes, previous, refreshed_bytes, refreshed = archive
    _validate_embedded_states(
        previous,
        refreshed,
        expected_lineage=expected_state_lineage,
        expected_serial=expected_state_serial,
    )
    return archive


def _build_receipt(
    *,
    saved_plan_bytes: bytes,
    source: SourceSnapshot,
    archive: tuple[bytes, Mapping[str, Any], bytes, Mapping[str, Any]],
    plan_json_bytes: bytes,
    expected_state_lineage: str,
    expected_state_serial: int,
    tofu_sha256: str,
    provider_sha256: str,
    provider_package_h1: str,
    now: datetime,
) -> Mapping[str, Any]:
    previous_bytes, _previous, refreshed_bytes, _refreshed = archive
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
        "open_tofu_provider_package_h1": provider_package_h1,
        "saved_plan_sha256": _sha256_bytes(saved_plan_bytes),
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


def _require_receipt_fresh(receipt: Mapping[str, Any], *, now: datetime) -> None:
    cleanup = datetime.strptime(
        str(receipt["cleanup_not_after"]), "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=UTC)
    if now.astimezone(UTC) >= cleanup:
        raise BootstrapPlanError(
            "saved plan cleanup deadline expired during validation"
        )


def validate_saved_plan(
    *,
    saved_plan: Path,
    provider_package: Path,
    expected_saved_plan_sha256: str,
    expected_source_commit: str,
    expected_state_lineage: str,
    expected_state_serial: int,
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
    saved_plan_bytes = _read_protected_file(
        saved_plan, label="saved plan", maximum_bytes=MAXIMUM_ARTIFACT_BYTES
    )
    if _sha256_bytes(saved_plan_bytes) != expected_saved_plan_sha256:
        raise BootstrapPlanError("saved plan digest does not match reviewed evidence")
    source_before = _source_snapshot(expected_source_commit)
    archive = _validate_archive(
        saved_plan_bytes=saved_plan_bytes,
        expected_saved_plan_sha256=expected_saved_plan_sha256,
        source=source_before,
        expected_state_lineage=expected_state_lineage,
        expected_state_serial=expected_state_serial,
    )
    decoded = _decode_saved_plan(
        saved_plan_bytes,
        source=source_before,
        provider_package=provider_package,
    )
    receipt = _build_receipt(
        saved_plan_bytes=saved_plan_bytes,
        source=source_before,
        archive=archive,
        plan_json_bytes=decoded.plan_json,
        expected_state_lineage=expected_state_lineage,
        expected_state_serial=expected_state_serial,
        tofu_sha256=decoded.tofu_sha256,
        provider_sha256=decoded.provider_sha256,
        provider_package_h1=decoded.provider_package_h1,
        now=_utc_now(),
    )
    if _source_snapshot(expected_source_commit) != source_before:
        raise BootstrapPlanError("bootstrap plan source changed during validation")
    _require_receipt_fresh(receipt, now=_utc_now())
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saved-plan", required=True, type=Path)
    parser.add_argument("--provider-package-dir", required=True, type=Path)
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
            provider_package=args.provider_package_dir,
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
