#!/opt/homebrew/bin/python3 -IS
"""Founder-operated one-use consumer for the staging base state-refresh plan.

The TTY ceremony prevents accidental invocation; it is not cryptographic proof
of human identity. The protected local operator account is a trust boundary.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import resource
import secrets
import select
import stat
import sys
import tempfile
import time
import types
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn

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
_BOOTSTRAP_DIGESTS = {
    _VALIDATOR.name: "e08ba655385999404d1b7be9a1107fd6ccf164a92608d45f174bf4b88ea90fa4",
}
_MAX_BOOTSTRAP_SOURCE_BYTES = 1024 * 1024


def _read_bootstrap_source(path: Path, expected_sha256: str) -> bytes:
    directory: int | None = None
    descriptor: int | None = None
    try:
        directory = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_before = os.fstat(directory)
        if (
            not stat.S_ISDIR(directory_before.st_mode)
            or directory_before.st_uid != os.geteuid()
            or stat.S_IMODE(directory_before.st_mode) & 0o022
        ):
            raise RuntimeError("bootstrap source refused")
        directory_identity = (
            directory_before.st_dev,
            directory_before.st_ino,
            directory_before.st_mode,
            directory_before.st_uid,
            directory_before.st_nlink,
            directory_before.st_mtime_ns,
            directory_before.st_ctime_ns,
        )
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 0 < before.st_size <= _MAX_BOOTSTRAP_SOURCE_BYTES
        ):
            raise RuntimeError("bootstrap source refused")
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
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
        directory_after = os.fstat(directory)
        if (
            remaining
            or len(value) != before.st_size
            or identity
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or directory_identity
            != (
                directory_after.st_dev,
                directory_after.st_ino,
                directory_after.st_mode,
                directory_after.st_uid,
                directory_after.st_nlink,
                directory_after.st_mtime_ns,
                directory_after.st_ctime_ns,
            )
            or hashlib.sha256(value).hexdigest() != expected_sha256
        ):
            raise RuntimeError("bootstrap source refused")
        return value
    except OSError as exc:
        raise RuntimeError("bootstrap source refused") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


try:
    refresh_source = _read_bootstrap_source(
        _VALIDATOR, _BOOTSTRAP_DIGESTS[_VALIDATOR.name]
    )
    refresh = types.ModuleType("_cosmaneura_state_refresh_validator")
    refresh.__file__ = str(_VALIDATOR)
    sys.modules[refresh.__name__] = refresh
    exec(
        compile(refresh_source, str(_VALIDATOR), "exec", dont_inherit=True),
        refresh.__dict__,
    )
    recovery = refresh.recovery
except Exception:
    print("state_refresh_apply_refused", file=sys.stderr)
    raise SystemExit(2)
CONSUMER_PATH = Path(__file__).resolve()
HASH_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
CONFIRMATION_SECONDS = 120.0
MAX_PROMPT_BYTES = 256
MAX_CLAIM_BYTES = 16 * 1024
MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
CLAIM_NAME_PREFIX = ".base-state-refresh-apply-claimed-v1-"
EXECUTABLE_SNAPSHOT_PREFIX = ".state-refresh-tofu-"
APPLY_LOG_NAME = "state-refresh-apply.log"
EXECUTABLE_SNAPSHOT_RE = re.compile(
    rf"{re.escape(EXECUTABLE_SNAPSHOT_PREFIX)}[0-9a-f]{{64}}-[0-9a-f]{{32}}"
)
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
        if mode & 0o022:
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
    tty: int | None = None
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
        read_write_tty = next(
            (
                fd
                for fd in descriptors
                if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR
            ),
            None,
        )
        if read_write_tty is None:
            _fail("operator.tty_access_invalid")
        # Duplicate the terminal we have already authenticated instead of
        # reopening /dev/tty. Sandboxed macOS terminals can deny that path
        # open even when all inherited descriptors are a valid foreground
        # TTY; duplicating its descriptor is also immune to path substitution.
        tty = os.dup(read_write_tty)
        os.set_inheritable(tty, False)
        metadata = os.fstat(tty)
        if (
            not stat.S_ISCHR(metadata.st_mode)
            or (metadata.st_dev, metadata.st_rdev) != identities[0]
            or os.tcgetpgrp(tty) != os.getpgrp()
            or os.get_inheritable(tty)
        ):
            _fail("operator.tty_mismatch")
        return tty
    except ConsumerError:
        if tty is not None:
            try:
                os.close(tty)
            except OSError:
                pass
        raise
    except (OSError, ValueError):
        if tty is not None:
            try:
                os.close(tty)
            except OSError:
                pass
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
        metadata.st_ctime_ns,
    )


def _open_pinned_plan(saved_plan: Path, *, expected_sha256: str) -> int:
    parent = recovery._open_protected_directory(
        saved_plan.parent, label="saved state-refresh plan directory"
    )
    source: int | None = None
    pinned: int | None = None
    try:
        source = os.open(
            saved_plan.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        before = os.fstat(source)
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
        with tempfile.TemporaryFile(
            prefix=".state-refresh-plan-", dir=saved_plan.parent
        ) as anonymous:
            anonymous_descriptor = anonymous.fileno()
            os.fchmod(anonymous_descriptor, 0o400)
            remaining = before.st_size
            while remaining:
                chunk = os.read(source, min(1024 * 1024, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                _write_all(anonymous_descriptor, chunk)
                remaining -= len(chunk)
            os.fsync(anonymous_descriptor)
            snapshot = os.fstat(anonymous_descriptor)
            if (
                remaining
                or _metadata_identity(os.fstat(source)) != identity
                or digest.hexdigest() != expected_sha256
                or not stat.S_ISREG(snapshot.st_mode)
                or snapshot.st_uid != os.geteuid()
                or snapshot.st_nlink != 0
                or stat.S_IMODE(snapshot.st_mode) != 0o400
                or snapshot.st_size != before.st_size
                or os.lseek(anonymous_descriptor, 0, os.SEEK_SET) != 0
            ):
                _fail("saved_plan.changed")
            pinned = os.dup(anonymous_descriptor)
        os.set_inheritable(pinned, True)
        if not os.get_inheritable(pinned):
            _fail("saved_plan.unavailable")
        result = pinned
        pinned = None
        return result
    except OSError:
        _fail("saved_plan.changed")
    except Exception:
        raise
    finally:
        if pinned is not None:
            os.close(pinned)
        if source is not None:
            os.close(source)
        os.close(parent)


def _revalidate_pinned_plan(descriptor: int, *, expected_sha256: str) -> None:
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 0
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


def _open_pinned_executable(
    path: Path, *, snapshot_directory: Path, expected_sha256: str
) -> tuple[Path, int]:
    source: int | None = None
    writer: int | None = None
    descriptor: int | None = None
    parent = recovery._open_protected_directory(
        snapshot_directory, label="execution directory"
    )
    snapshot_name = (
        f"{EXECUTABLE_SNAPSHOT_PREFIX}{expected_sha256}-{secrets.token_hex(16)}"
    )
    snapshot_path = snapshot_directory / snapshot_name
    created = False
    completed = False
    try:
        source = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(source)
        identity = _metadata_identity(before)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not before.st_mode & 0o111
            or not 0 < before.st_size <= MAX_EXECUTABLE_BYTES
        ):
            _fail("runtime.changed")
        writer = os.open(
            snapshot_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o500,
            dir_fd=parent,
        )
        created = True
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(source, min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            _write_all(writer, chunk)
            remaining -= len(chunk)
        os.fchmod(writer, 0o500)
        os.fsync(writer)
        written = os.fstat(writer)
        if (
            remaining
            or _metadata_identity(os.fstat(source)) != identity
            or digest.hexdigest() != expected_sha256
            or not stat.S_ISREG(written.st_mode)
            or written.st_uid != os.geteuid()
            or written.st_nlink != 1
            or stat.S_IMODE(written.st_mode) != 0o500
            or written.st_size != before.st_size
        ):
            _fail("runtime.changed")
        os.fsync(parent)
        descriptor = os.open(
            snapshot_name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        os.close(writer)
        writer = None
        _revalidate_pinned_executable(
            descriptor,
            snapshot_path=snapshot_path,
            expected_sha256=expected_sha256,
        )
        if os.get_inheritable(descriptor):
            _fail("runtime.changed")
        result = descriptor
        descriptor = None
        completed = True
        return snapshot_path, result
    except OSError:
        _fail("runtime.changed")
    finally:
        if created and not completed:
            reference = descriptor if descriptor is not None else writer
            if reference is not None:
                _unlink_matching_executable_snapshot(
                    parent,
                    snapshot_name=snapshot_name,
                    descriptor=reference,
                )
        if descriptor is not None:
            os.close(descriptor)
        if writer is not None:
            os.close(writer)
        if source is not None:
            os.close(source)
        os.close(parent)


def _unlink_matching_executable_snapshot(
    parent: int, *, snapshot_name: str, descriptor: int
) -> None:
    if EXECUTABLE_SNAPSHOT_RE.fullmatch(snapshot_name) is None:
        _fail("runtime.changed")
    try:
        path_metadata = os.stat(snapshot_name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(path_metadata.st_mode) or _metadata_identity(
            path_metadata
        ) != _metadata_identity(os.fstat(descriptor)):
            _fail("runtime.changed")
        os.unlink(snapshot_name, dir_fd=parent)
        os.fsync(parent)
    except OSError:
        _fail("runtime.changed")


def _remove_unclaimed_executable_snapshot(snapshot_path: Path, descriptor: int) -> None:
    parent = recovery._open_protected_directory(
        snapshot_path.parent, label="execution directory"
    )
    try:
        _unlink_matching_executable_snapshot(
            parent, snapshot_name=snapshot_path.name, descriptor=descriptor
        )
    finally:
        os.close(parent)


def _revalidate_pinned_executable(
    descriptor: int, *, snapshot_path: Path, expected_sha256: str
) -> None:
    try:
        before = os.fstat(descriptor)
        identity = _metadata_identity(before)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o500
            or not 0 < before.st_size <= MAX_EXECUTABLE_BYTES
            or os.lseek(descriptor, 0, os.SEEK_SET) != 0
        ):
            _fail("runtime.changed")
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
            _fail("runtime.changed")
        path_metadata = snapshot_path.lstat()
        if stat.S_ISLNK(path_metadata.st_mode) or _metadata_identity(
            path_metadata
        ) != _metadata_identity(before):
            _fail("runtime.changed")
    except OSError:
        _fail("runtime.changed")


def _claim_name(expected_saved_plan_sha256: str) -> str:
    if HASH_RE.fullmatch(expected_saved_plan_sha256) is None:
        _fail("claim.invalid")
    return f"{CLAIM_NAME_PREFIX}{expected_saved_plan_sha256}"


def _claim(
    plan_directory: Path,
    expected_saved_plan_sha256: str,
    facts: Mapping[str, Any],
    *,
    on_created: Callable[[], None] | None = None,
) -> None:
    payload = _canonical(facts) + b"\n"
    if len(payload) > MAX_CLAIM_BYTES:
        _fail("claim.invalid")
    parent = recovery._open_protected_directory(
        plan_directory, label="saved state-refresh plan directory"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            _claim_name(expected_saved_plan_sha256),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=parent,
        )
        if on_created is not None:
            on_created()
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


def _protected_directory_identity(path: Path) -> Mapping[str, int]:
    descriptor = recovery._open_protected_directory(path, label="execution directory")
    try:
        metadata = os.fstat(descriptor)
        return {
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
        }
    finally:
        os.close(descriptor)


def _apply_log_identity(path: Path, descriptor: int) -> Mapping[str, int | str]:
    try:
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = path.lstat()
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    except (OSError, ValueError):
        _fail("apply_log.changed")
    if (
        path.name != APPLY_LOG_NAME
        or stat.S_ISLNK(path_metadata.st_mode)
        or not stat.S_ISREG(descriptor_metadata.st_mode)
        or not stat.S_ISREG(path_metadata.st_mode)
        or descriptor_metadata.st_uid != os.geteuid()
        or descriptor_metadata.st_nlink != 1
        or stat.S_IMODE(descriptor_metadata.st_mode) != 0o600
        or descriptor_flags & os.O_ACCMODE != os.O_WRONLY
        or not descriptor_flags & os.O_APPEND
        or _metadata_identity(descriptor_metadata) != _metadata_identity(path_metadata)
    ):
        _fail("apply_log.changed")
    return {
        "name": APPLY_LOG_NAME,
        "device": descriptor_metadata.st_dev,
        "inode": descriptor_metadata.st_ino,
        "mode": stat.S_IMODE(descriptor_metadata.st_mode),
        "uid": descriptor_metadata.st_uid,
    }


def _open_apply_log(
    execution_dir: Path,
) -> tuple[Path, int, Mapping[str, int | str]]:
    path = execution_dir / APPLY_LOG_NAME
    parent = recovery._open_protected_directory(
        execution_dir, label="execution directory"
    )
    descriptor: int | None = None
    created = False
    completed = False
    try:
        descriptor = os.open(
            APPLY_LOG_NAME,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        created = True
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.fsync(parent)
        identity = _apply_log_identity(path, descriptor)
        result = descriptor
        descriptor = None
        completed = True
        return path, result, identity
    except OSError:
        _fail("apply_log.unavailable")
    finally:
        try:
            if created and not completed and descriptor is not None:
                _unlink_matching_empty_apply_log(parent, descriptor=descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)


def _unlink_matching_empty_apply_log(parent: int, *, descriptor: int) -> None:
    try:
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = os.stat(APPLY_LOG_NAME, dir_fd=parent, follow_symlinks=False)
        if (
            stat.S_ISLNK(path_metadata.st_mode)
            or not stat.S_ISREG(descriptor_metadata.st_mode)
            or descriptor_metadata.st_uid != os.geteuid()
            or descriptor_metadata.st_nlink != 1
            or stat.S_IMODE(descriptor_metadata.st_mode) != 0o600
            or descriptor_metadata.st_size != 0
            or _metadata_identity(descriptor_metadata)
            != _metadata_identity(path_metadata)
        ):
            _fail("apply_log.changed")
        os.unlink(APPLY_LOG_NAME, dir_fd=parent)
        os.fsync(parent)
    except OSError:
        _fail("apply_log.changed")


def _remove_unclaimed_apply_log(path: Path, descriptor: int) -> None:
    if path.name != APPLY_LOG_NAME:
        _fail("apply_log.changed")
    parent = recovery._open_protected_directory(
        path.parent, label="execution directory"
    )
    try:
        _unlink_matching_empty_apply_log(parent, descriptor=descriptor)
    finally:
        os.close(parent)


def _require_before_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("operator.confirmation_timeout")


def _redirect_standard_streams(apply_log_descriptor: int) -> None:
    descriptor: int | None = None
    try:
        log_metadata = os.fstat(apply_log_descriptor)
        if (
            not stat.S_ISREG(log_metadata.st_mode)
            or log_metadata.st_uid != os.geteuid()
            or log_metadata.st_nlink != 1
            or stat.S_IMODE(log_metadata.st_mode) != 0o600
        ):
            _fail("apply_log.changed")
        descriptor = os.open(
            os.devnull,
            os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        if not stat.S_ISCHR(os.fstat(descriptor).st_mode):
            _fail("runtime.changed")
        os.dup2(descriptor, 0, inheritable=True)
        for target in (1, 2):
            os.dup2(apply_log_descriptor, target, inheritable=True)
    except OSError:
        _fail("runtime.changed")
    finally:
        if descriptor is not None and descriptor > 2:
            os.close(descriptor)


def _close_ambient_descriptors(preserved: set[int]) -> None:
    if any(fd <= 2 for fd in preserved) or len(preserved) != 2:
        _fail("runtime.changed")
    try:
        for descriptor in preserved:
            os.fstat(descriptor)
        maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        if maximum == resource.RLIM_INFINITY:
            maximum = 1024 * 1024
        start = 3
        for descriptor in sorted(preserved):
            os.closerange(start, descriptor)
            start = descriptor + 1
        os.closerange(start, int(maximum))
        for descriptor in preserved:
            os.fstat(descriptor)
        unexpected: list[int] = []
        for name in os.listdir("/dev/fd"):
            if not name.isdecimal():
                continue
            descriptor = int(name)
            if descriptor <= 2 or descriptor in preserved:
                continue
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            unexpected.append(descriptor)
        if unexpected:
            _fail("runtime.changed")
    except OSError:
        _fail("runtime.changed")


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
        if _consumer_source_digest(expected_source_commit) != consumer_sha256:
            _fail("consumer_source.changed")
        source = refresh._source_snapshot(expected_source_commit)
        if source.tree_sha256 != receipt.get("source_tree_sha256"):
            _fail("source.changed")
        plan_descriptor = _open_pinned_plan(
            saved_plan, expected_sha256=expected_saved_plan_sha256
        )
        tofu_descriptor: int | None = None
        tofu_snapshot: Path | None = None
        apply_log_path: Path | None = None
        apply_log_descriptor: int | None = None
        claim_created = False

        def record_claim_created() -> None:
            nonlocal claim_created
            claim_created = True

        try:
            tofu_sha256 = receipt.get("open_tofu_sha256")
            if not isinstance(tofu_sha256, str):
                _fail("validation_receipt.invalid")
            tofu_snapshot, tofu_descriptor = _open_pinned_executable(
                tofu,
                snapshot_directory=execution_dir,
                expected_sha256=tofu_sha256,
            )
            remote_bytes = refresh._pull_remote_state(
                tofu=tofu_snapshot,
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
            if recovery._provider_tree_sha256(execution_dir) != receipt.get(
                "execution_provider_tree_sha256"
            ) or recovery._backend_snapshot(
                tofu=tofu_snapshot, data_dir=execution_dir
            ) != receipt.get("backend_metadata_sha256"):
                _fail("runtime.changed")
            execution_directory_identity = _protected_directory_identity(execution_dir)
            apply_log_path, apply_log_descriptor, apply_log_identity = _open_apply_log(
                execution_dir
            )
            exec_path = str(tofu_snapshot)
            argv = [
                exec_path,
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
            deadline = _confirm_operator(
                expected_source_commit,
                expected_saved_plan_sha256,
                challenge_source=challenge_source,
            )
            tty = _validate_operator_tty()
            os.close(tty)
            _revalidate_pinned_plan(
                plan_descriptor, expected_sha256=expected_saved_plan_sha256
            )
            _revalidate_pinned_executable(
                tofu_descriptor,
                snapshot_path=tofu_snapshot,
                expected_sha256=tofu_sha256,
            )
            final_source = refresh._source_snapshot(expected_source_commit)
            if (
                final_source.tree_sha256 != receipt.get("source_tree_sha256")
                or _protected_directory_identity(execution_dir)
                != execution_directory_identity
                or recovery._provider_tree_sha256(execution_dir)
                != receipt.get("execution_provider_tree_sha256")
                or recovery._backend_snapshot(
                    tofu=tofu_snapshot, data_dir=execution_dir
                )
                != receipt.get("backend_metadata_sha256")
                or recovery._executable_sha256(azure_cli, label="Azure CLI")
                != azure_cli_sha256
            ):
                _fail("runtime.changed")
            _require_before_deadline(deadline)
            _claim(
                saved_plan.parent,
                expected_saved_plan_sha256,
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
                    "execution_directory_identity": execution_directory_identity,
                    "apply_log_identity": apply_log_identity,
                    "apply_outcome": "unknown",
                },
                on_created=record_claim_created,
            )
            _require_before_deadline(deadline)
            if (
                apply_log_descriptor is None
                or _apply_log_identity(apply_log_path, apply_log_descriptor)
                != apply_log_identity
            ):
                _fail("apply_log.changed")
            _redirect_standard_streams(apply_log_descriptor)
            os.close(apply_log_descriptor)
            apply_log_descriptor = None
            _close_ambient_descriptors({plan_descriptor, tofu_descriptor})
            if not os.get_inheritable(plan_descriptor) or os.get_inheritable(
                tofu_descriptor
            ):
                _fail("runtime.changed")
            _revalidate_pinned_plan(
                plan_descriptor, expected_sha256=expected_saved_plan_sha256
            )
            _revalidate_pinned_executable(
                tofu_descriptor,
                snapshot_path=tofu_snapshot,
                expected_sha256=tofu_sha256,
            )
            if (
                _protected_directory_identity(execution_dir)
                != execution_directory_identity
            ):
                _fail("runtime.changed")
            _require_before_deadline(deadline)
            os.execve(exec_path, argv, environment)
        finally:
            try:
                if apply_log_descriptor is not None:
                    try:
                        if apply_log_path is not None and not claim_created:
                            _remove_unclaimed_apply_log(
                                apply_log_path, apply_log_descriptor
                            )
                    finally:
                        os.close(apply_log_descriptor)
            finally:
                try:
                    if tofu_descriptor is not None:
                        try:
                            if tofu_snapshot is not None and not claim_created:
                                _remove_unclaimed_executable_snapshot(
                                    tofu_snapshot, tofu_descriptor
                                )
                        finally:
                            os.close(tofu_descriptor)
                finally:
                    os.close(plan_descriptor)
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
