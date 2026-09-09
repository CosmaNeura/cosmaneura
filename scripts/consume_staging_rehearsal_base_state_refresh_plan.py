#!/opt/homebrew/bin/python3 -IS
"""Human-only one-use consumer for the staging base state-refresh plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import secrets
import select
import stat
import sys
import time
import types
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn, cast

if __name__ == "__main__" and (
    sys.flags.isolated != 1 or sys.flags.no_site != 1 or not sys.flags.safe_path
):
    print("state_refresh_apply_refused", file=sys.stderr)
    raise SystemExit(2)

_VALIDATOR = (
    Path(__file__)
    .resolve()
    .with_name("validate_staging_rehearsal_base_state_refresh_plan.py")
)
_RECOVERY_VALIDATOR = _VALIDATOR.with_name(
    "validate_staging_rehearsal_base_recovery_plan.py"
)
_RECOVERY_QUALIFIED_NAME = "scripts.validate_staging_rehearsal_base_recovery_plan"
_RECOVERY_ATTRIBUTE = "validate_staging_rehearsal_base_recovery_plan"
_MISSING = object()
try:
    recovery = types.ModuleType("validate_staging_rehearsal_base_recovery_plan")
    recovery.__file__ = str(_RECOVERY_VALIDATOR)
    sys.modules[recovery.__name__] = recovery
    exec(
        compile(
            _RECOVERY_VALIDATOR.read_bytes(),
            str(_RECOVERY_VALIDATOR),
            "exec",
            dont_inherit=True,
        ),
        recovery.__dict__,
    )
    previous_qualified = sys.modules.get(_RECOVERY_QUALIFIED_NAME, _MISSING)
    scripts_package = sys.modules.get("scripts")
    previous_attribute = (
        getattr(scripts_package, _RECOVERY_ATTRIBUTE, _MISSING)
        if scripts_package is not None
        else _MISSING
    )
    sys.modules[_RECOVERY_QUALIFIED_NAME] = recovery
    if scripts_package is not None:
        setattr(scripts_package, _RECOVERY_ATTRIBUTE, recovery)
    try:
        refresh = types.ModuleType("_cosmaneura_state_refresh_validator")
        refresh.__file__ = str(_VALIDATOR)
        sys.modules[refresh.__name__] = refresh
        exec(
            compile(
                _VALIDATOR.read_bytes(), str(_VALIDATOR), "exec", dont_inherit=True
            ),
            refresh.__dict__,
        )
    finally:
        loaded_scripts_package = sys.modules.get("scripts")
        if loaded_scripts_package is not None:
            if previous_attribute is _MISSING:
                if (
                    getattr(loaded_scripts_package, _RECOVERY_ATTRIBUTE, _MISSING)
                    is recovery
                ):
                    delattr(loaded_scripts_package, _RECOVERY_ATTRIBUTE)
            else:
                setattr(loaded_scripts_package, _RECOVERY_ATTRIBUTE, previous_attribute)
        if previous_qualified is _MISSING:
            sys.modules.pop(_RECOVERY_QUALIFIED_NAME, None)
        else:
            sys.modules[_RECOVERY_QUALIFIED_NAME] = cast(
                types.ModuleType, previous_qualified
            )
except Exception:
    print("state_refresh_apply_refused", file=sys.stderr)
    raise SystemExit(2)

if refresh.recovery is not recovery:
    print("state_refresh_apply_refused", file=sys.stderr)
    raise SystemExit(2)
CONSUMER_PATH = Path(__file__).resolve()
HASH_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
CONFIRMATION_SECONDS = 120.0
MAX_PROMPT_BYTES = 256
MAX_CLAIM_BYTES = 16 * 1024
CLAIM_NAME = ".base-state-refresh-apply-claimed-v1"
AZURE_CLI_ENTRYPOINTS = (
    Path("/opt/homebrew/bin/az"),
    Path("/usr/local/bin/az"),
    Path("/usr/bin/az"),
)
PYTHON_ENTRYPOINTS = (
    Path("/opt/homebrew/bin/python3"),
    Path("/usr/local/bin/python3"),
    Path("/usr/bin/python3"),
)
CHANGE_STATEMENT = (
    "APPLY STAGING-REHEARSAL STATE REFRESH SERIAL 8 TO 9: AZURE ACTIONS 0"
)


class ConsumerError(ValueError):
    """A bounded refusal that never includes state, plan, or credential values."""


def _fail(code: str) -> NoReturn:
    raise ConsumerError(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _consumer_source_digest(expected_commit: str) -> str:
    relative = CONSUMER_PATH.relative_to(recovery.REPOSITORY_ROOT)
    value = CONSUMER_PATH.read_bytes()
    if recovery._run_git(["status", "--porcelain=v1", "--", str(relative)]):
        _fail("consumer_source.dirty")
    if recovery._run_git(["show", f"{expected_commit}:{relative}"]) != value:
        _fail("consumer_source.changed")
    return _digest(value)


def _reject_ambient_environment() -> None:
    forbidden_exact = {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
        "no_proxy",
    }
    forbidden_prefixes = (
        "PYTHON",
        "TF_",
        "TOFU_",
        "OPENTOFU_",
        "GIT_",
        "ARM_",
        "AZURE_",
    )
    if (
        sys.gettrace() is not None
        or sys.getprofile() is not None
        or sys.prefix != sys.base_prefix
        or any(
            name in forbidden_exact or name.startswith(forbidden_prefixes)
            for name in os.environ
        )
    ):
        _fail("environment.unsafe")


def _python_runtime_sha256() -> str:
    if (
        sys.flags.isolated != 1
        or sys.flags.no_site != 1
        or not sys.flags.safe_path
        or not (sys.version_info >= (3, 11) and sys.version_info < (4, 0))
    ):
        _fail("python_runtime.unsafe")
    try:
        executable = Path(sys.executable).resolve(strict=True)
        allowed = {
            entrypoint.resolve(strict=True)
            for entrypoint in PYTHON_ENTRYPOINTS
            if entrypoint.exists()
        }
    except OSError:
        _fail("python_runtime.unavailable")
    if executable not in allowed:
        _fail("python_runtime.untrusted")
    _validate_path_ancestry(executable)
    metadata = executable.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not os.access(executable, os.X_OK)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        _fail("python_runtime.unsafe")
    return recovery._executable_sha256(executable, label="Python")


def _validate_path_ancestry(path: Path, *, leaf_directory: bool = False) -> None:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        _fail("local_path.invalid")
    current = Path(path.anchor)
    for index, part in enumerate(path.parts[1:]):
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            _fail("local_path.unavailable")
        leaf = index == len(path.parts[1:]) - 1
        if metadata.st_uid not in {0, os.geteuid()}:
            _fail("local_path.unsafe")
        if leaf and current.is_symlink():
            continue
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o002 or (mode & 0o020 and metadata.st_uid != os.geteuid()):
            _fail("local_path.unsafe")
        if (leaf and leaf_directory and not stat.S_ISDIR(metadata.st_mode)) or (
            not leaf and not stat.S_ISDIR(metadata.st_mode)
        ):
            _fail("local_path.invalid")


def _resolve_azure_cli() -> Path:
    for entrypoint in AZURE_CLI_ENTRYPOINTS:
        if not entrypoint.exists():
            continue
        _validate_path_ancestry(entrypoint)
        try:
            executable = entrypoint.resolve(strict=True)
        except OSError:
            _fail("azure_cli.unavailable")
        _validate_path_ancestry(executable)
        metadata = executable.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not os.access(executable, os.X_OK)
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            _fail("azure_cli.unsafe")
        return executable
    _fail("azure_cli.unavailable")


def _operator_paths() -> tuple[Path, Path]:
    try:
        record = pwd.getpwuid(os.geteuid())
    except KeyError:
        _fail("operator.identity_invalid")
    home = Path(record.pw_dir).resolve()
    try:
        azure = (home / ".azure").resolve(strict=True)
    except OSError:
        _fail("operator.azure_config_invalid")
    _validate_path_ancestry(home, leaf_directory=True)
    _validate_path_ancestry(azure, leaf_directory=True)
    return home, azure


@contextmanager
def _pinned_process_environment(
    *, home: Path, azure_config: Path, azure_cli: Path
) -> Any:
    original = dict(os.environ)
    path = ":".join(
        dict.fromkeys(
            (
                str(azure_cli.parent),
                "/opt/homebrew/bin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            )
        )
    )
    os.environ.clear()
    os.environ.update(
        {
            "HOME": str(home),
            "PATH": path,
            "LC_ALL": "C",
            "LANG": "C",
            "AZURE_CONFIG_DIR": str(azure_config),
        }
    )
    try:
        if (Path(path.split(":", 1)[0]) / "az").resolve(strict=True) != azure_cli:
            _fail("azure_cli.path_mismatch")
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def _validate_operator_tty() -> int:
    if os.getuid() == 0 or os.getuid() != os.geteuid() or os.getgid() != os.getegid():
        _fail("operator.identity_invalid")
    descriptors = (0, 1, 2)
    try:
        if not all(os.isatty(fd) for fd in descriptors):
            _fail("operator.tty_required")
        identities = [
            (item.st_dev, item.st_rdev) for item in map(os.fstat, descriptors)
        ]
        if len(set(identities)) != 1 or any(
            os.tcgetpgrp(fd) != os.getpgrp() for fd in descriptors
        ):
            _fail("operator.tty_mismatch")
        tty = os.open(
            "/dev/tty",
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOCTTY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(tty)
        if (
            not stat.S_ISCHR(metadata.st_mode)
            or (metadata.st_dev, metadata.st_rdev) != identities[0]
            or os.tcgetpgrp(tty) != os.getpgrp()
        ):
            os.close(tty)
            _fail("operator.tty_mismatch")
        return tty
    except (OSError, ValueError):
        _fail("operator.tty_required")


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            _fail("operator.tty_failed")
        offset += written


def _read_prompt(descriptor: int, prompt: str, deadline: float) -> str:
    _write_all(descriptor, prompt.encode("ascii"))
    value = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail("operator.confirmation_timeout")
        ready, _, _ = select.select([descriptor], [], [], remaining)
        if not ready:
            _fail("operator.confirmation_timeout")
        byte = os.read(descriptor, 1)
        if not byte:
            _fail("operator.confirmation_eof")
        if byte == b"\n":
            break
        if byte in {b"\r", b"\x00"} or len(value) >= MAX_PROMPT_BYTES:
            _fail("operator.confirmation_invalid")
        value.extend(byte)
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        _fail("operator.confirmation_invalid")


def _confirm_operator(
    expected_source_commit: str,
    expected_saved_plan_sha256: str,
    *,
    challenge_source: Callable[[int], bytes] = secrets.token_bytes,
) -> float:
    challenge_bytes = challenge_source(8)
    if type(challenge_bytes) is not bytes or len(challenge_bytes) != 8:
        _fail("operator.challenge_invalid")
    challenge = challenge_bytes.hex().upper()
    expected = (
        expected_source_commit,
        expected_saved_plan_sha256,
        CHANGE_STATEMENT,
        f"APPLY {challenge}",
    )
    prompts = (
        f"STAGING STATE ONLY. Type exact commit {expected_source_commit}: ",
        f"Type exact saved-plan SHA-256 {expected_saved_plan_sha256}: ",
        "Type the exact state-refresh statement: ",
        f"Type APPLY {challenge}: ",
    )
    deadline = time.monotonic() + CONFIRMATION_SECONDS
    tty = _validate_operator_tty()
    try:
        for prompt, exact in zip(prompts, expected, strict=True):
            if _read_prompt(tty, prompt, deadline) != exact:
                _fail("operator.confirmation_mismatch")
        if time.monotonic() >= deadline:
            _fail("operator.confirmation_timeout")
    finally:
        os.close(tty)
    return deadline


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _open_pinned_plan(saved_plan: Path, *, expected_sha256: str) -> int:
    parent = recovery._open_protected_directory(
        saved_plan.parent, label="saved state-refresh plan directory"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            saved_plan.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or not 0 < before.st_size <= recovery.MAXIMUM_ARTIFACT_BYTES
        ):
            _fail("saved_plan.changed")
        identity = _metadata_identity(before)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        if (
            remaining
            or _metadata_identity(os.fstat(descriptor)) != identity
            or digest.hexdigest() != expected_sha256
            or os.lseek(descriptor, 0, os.SEEK_SET) != 0
        ):
            _fail("saved_plan.changed")
        os.set_inheritable(descriptor, True)
        if not os.get_inheritable(descriptor):
            _fail("saved_plan.unavailable")
        return descriptor
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(parent)


def _revalidate_pinned_plan(descriptor: int, *, expected_sha256: str) -> None:
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or not 0 < before.st_size <= recovery.MAXIMUM_ARTIFACT_BYTES
            or os.lseek(descriptor, 0, os.SEEK_SET) != 0
        ):
            _fail("saved_plan.changed")
        identity = _metadata_identity(before)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        if (
            remaining
            or _metadata_identity(os.fstat(descriptor)) != identity
            or digest.hexdigest() != expected_sha256
            or os.lseek(descriptor, 0, os.SEEK_SET) != 0
        ):
            _fail("saved_plan.changed")
    except OSError:
        _fail("saved_plan.changed")


def _claim(execution_dir: Path, facts: Mapping[str, Any]) -> None:
    payload = _canonical(facts) + b"\n"
    if len(payload) > MAX_CLAIM_BYTES:
        _fail("claim.invalid")
    parent = recovery._open_protected_directory(
        execution_dir, label="execution directory"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            CLAIM_NAME,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=parent,
        )
        _write_all(descriptor, payload)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_size != len(payload)
        ):
            _fail("claim.invalid")
        os.fsync(parent)
    except OSError:
        _fail("claim.exists_or_unavailable")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _execution_environment(
    *, home: Path, azure_config: Path, azure_cli: Path, execution_dir: Path
) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": ":".join(
            dict.fromkeys(
                (
                    str(azure_cli.parent),
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "/usr/bin",
                    "/bin",
                )
            )
        ),
        "LC_ALL": "C",
        "LANG": "C",
        "AZURE_CONFIG_DIR": str(azure_config),
        "AZURE_CORE_ONLY_SHOW_ERRORS": "true",
        "CHECKPOINT_DISABLE": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "TF_CLI_CONFIG_FILE": os.devnull,
        "TF_DATA_DIR": str(execution_dir),
        "TF_IN_AUTOMATION": "1",
        "ARM_SUBSCRIPTION_ID": recovery.EXPECTED_SUBSCRIPTION_ID,
        "ARM_TENANT_ID": recovery.EXPECTED_TENANT_ID,
        "ARM_ENVIRONMENT": "public",
        "ARM_USE_CLI": "true",
        "ARM_USE_AZUREAD": "true",
    }


def consume(
    *,
    saved_plan: Path,
    state_json: Path,
    execution_dir: Path,
    expected_source_commit: str,
    expected_saved_plan_sha256: str,
    challenge_source: Callable[[int], bytes] = secrets.token_bytes,
) -> NoReturn:
    if (
        COMMIT_RE.fullmatch(expected_source_commit) is None
        or HASH_RE.fullmatch(expected_saved_plan_sha256) is None
    ):
        _fail("expected_binding.invalid")
    _reject_ambient_environment()
    python_runtime_sha256 = _python_runtime_sha256()
    home, azure_config = _operator_paths()
    azure_cli = _resolve_azure_cli()
    azure_cli_sha256 = recovery._executable_sha256(azure_cli, label="Azure CLI")
    with _pinned_process_environment(
        home=home, azure_config=azure_config, azure_cli=azure_cli
    ):
        receipt = dict(
            refresh.revalidate_state_refresh_plan(
                saved_plan=saved_plan,
                state_json=state_json,
                execution_dir=execution_dir,
                expected_source_commit=expected_source_commit,
                expected_saved_plan_sha256=expected_saved_plan_sha256,
            )
        )
        expected_facts = {
            "policy_id": "cosmaneura-staging-rehearsal-base-state-refresh-plan-v1",
            "generation_mode": "digest_bound_revalidation",
            "source_commit": expected_source_commit,
            "state_json_sha256": refresh.EXPECTED_PRE_REFRESH_STATE_SHA256,
            "saved_plan_sha256": expected_saved_plan_sha256,
            "state_lineage": recovery.EXPECTED_LINEAGE,
            "pre_refresh_state_serial": refresh.PRE_REFRESH_SERIAL,
            "expected_post_refresh_state_serial": refresh.POST_REFRESH_SERIAL,
            "state_refresh_count": 10,
            "azure_resource_action_count": 0,
            "output_create_count": 1,
            "apply_authorized": False,
            "founder_apply_required": True,
            "pass": True,
        }
        if any(
            receipt.get(name) != value or type(receipt.get(name)) is not type(value)
            for name, value in expected_facts.items()
        ):
            _fail("validation_receipt.invalid")
        consumer_sha256 = _consumer_source_digest(expected_source_commit)
        deadline = _confirm_operator(
            expected_source_commit,
            expected_saved_plan_sha256,
            challenge_source=challenge_source,
        )
        state_bytes = recovery._read_protected_file(
            state_json, label="pre-refresh state snapshot", required_mode=0o600
        )
        refresh._validate_pre_refresh_state_digest(state_bytes)
        tofu = recovery._resolve_and_verify_tofu()
        _validate_path_ancestry(tofu)
        if str(tofu) != receipt.get("open_tofu_path") or recovery._executable_sha256(
            tofu, label="OpenTofu"
        ) != receipt.get("open_tofu_sha256"):
            _fail("runtime.changed")
        remote_bytes = refresh._pull_remote_state(
            tofu=tofu,
            data_dir=execution_dir,
        )
        state = recovery._load_json_bytes(
            state_bytes, label="pre-refresh state snapshot"
        )
        remote = recovery._load_json_bytes(
            remote_bytes, label="remote pre-refresh state"
        )
        if not refresh._json_equal(
            refresh._state_binding_projection(state),
            refresh._state_binding_projection(remote),
        ):
            _fail("remote_state.changed")
        if time.monotonic() >= deadline:
            _fail("operator.confirmation_timeout")
        if _consumer_source_digest(expected_source_commit) != consumer_sha256:
            _fail("consumer_source.changed")
        source = refresh._source_snapshot(expected_source_commit)
        if source.tree_sha256 != receipt.get("source_tree_sha256"):
            _fail("source.changed")
        plan_descriptor = _open_pinned_plan(
            saved_plan, expected_sha256=expected_saved_plan_sha256
        )
        if (
            recovery._provider_tree_sha256(execution_dir)
            != receipt.get("execution_provider_tree_sha256")
            or recovery._backend_snapshot(tofu=tofu, data_dir=execution_dir)
            != receipt.get("backend_metadata_sha256")
            or time.monotonic() >= deadline
        ):
            os.close(plan_descriptor)
            _fail("runtime.changed")
        argv = [
            str(tofu),
            f"-chdir={recovery.REHEARSAL_ROOT}",
            "apply",
            "-input=false",
            "-lock=true",
            "-lock-timeout=0s",
            "-no-color",
            f"/dev/fd/{plan_descriptor}",
        ]
        environment = _execution_environment(
            home=home,
            azure_config=azure_config,
            azure_cli=azure_cli,
            execution_dir=execution_dir,
        )
        tty = _validate_operator_tty()
        os.close(tty)
        if time.monotonic() >= deadline:
            os.close(plan_descriptor)
            _fail("operator.confirmation_timeout")
        _revalidate_pinned_plan(
            plan_descriptor, expected_sha256=expected_saved_plan_sha256
        )
        if (
            recovery._executable_sha256(tofu, label="OpenTofu")
            != receipt.get("open_tofu_sha256")
            or recovery._executable_sha256(azure_cli, label="Azure CLI")
            != azure_cli_sha256
        ):
            os.close(plan_descriptor)
            _fail("runtime.changed")
        _claim(
            execution_dir,
            {
                "schema_version": 1,
                "policy_id": "cosmaneura-staging-rehearsal-base-state-refresh-apply-claim-v1",
                "source_commit": expected_source_commit,
                "saved_plan_sha256": expected_saved_plan_sha256,
                "consumer_source_sha256": consumer_sha256,
                "python_runtime_sha256": python_runtime_sha256,
                "azure_cli_sha256": azure_cli_sha256,
                "validation_receipt_sha256": _digest(_canonical(receipt)),
                "argv_sha256": _digest(_canonical(argv)),
                "environment_sha256": _digest(_canonical(environment)),
                "apply_outcome": "unknown",
            },
        )
        os.execve(str(tofu), argv, environment)
    _fail("execve.returned")


class _RefusingParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _fail("arguments.invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _RefusingParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--saved-plan", required=True, type=Path)
    parser.add_argument("--state-json", required=True, type=Path)
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-saved-plan-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        consume(
            saved_plan=args.saved_plan,
            state_json=args.state_json,
            execution_dir=args.execution_dir,
            expected_source_commit=args.expected_source_commit,
            expected_saved_plan_sha256=args.expected_saved_plan_sha256,
        )
    except (Exception, KeyboardInterrupt):
        print("state_refresh_apply_refused", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
