"""Fail-closed local I/O helpers for offline plan verification."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 128 * 1024 * 1024


class OfflineRuntimeError(ValueError):
    """Raised when local evidence or runtime isolation is not exact."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_file_set_sha256(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        encoded_name = name.encode("utf-8")
        value = files[name]
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OfflineRuntimeError("JSON contains duplicate object keys")
        result[key] = value
    return result


def reject_nonfinite_json(_value: str) -> None:
    raise OfflineRuntimeError("JSON contains a non-finite number")


def load_json_bytes(value: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=unique_json_object,
            parse_constant=reject_nonfinite_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise OfflineRuntimeError(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise OfflineRuntimeError(f"{label} must contain a JSON object")
    return decoded


def reject_symlink_components(
    path: Path, *, label: str, allow_missing_leaf: bool = False
) -> None:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise OfflineRuntimeError(f"{label} path must be absolute and normalized")
    current = Path(path.anchor)
    for index, part in enumerate(path.parts[1:]):
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as exc:
            if allow_missing_leaf and index == len(path.parts[1:]) - 1:
                return
            raise OfflineRuntimeError(f"{label} path is unavailable") from exc
        except OSError as exc:
            raise OfflineRuntimeError(f"{label} path is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise OfflineRuntimeError(f"{label} path contains a symbolic link")


def open_private_directory(path: Path, *, label: str) -> int:
    reject_symlink_components(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise OfflineRuntimeError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise OfflineRuntimeError(
            f"{label} must be an owner-controlled directory with mode 0700"
        )
    return descriptor


def require_private_directory(path: Path, *, label: str) -> None:
    os.close(open_private_directory(path, label=label))


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_descriptor(descriptor: int, *, size: int, label: str) -> bytes:
    value = bytearray()
    while len(value) < size:
        chunk = os.read(descriptor, min(1024 * 1024, size - len(value)))
        if not chunk:
            break
        value.extend(chunk)
    if len(value) != size:
        raise OfflineRuntimeError(f"{label} changed while it was read")
    return bytes(value)


def read_private_file(
    path: Path,
    *,
    label: str,
    required_mode: int = 0o400,
    maximum_bytes: int = MAX_FILE_BYTES,
) -> bytes:
    parent = open_private_directory(path.parent, label=f"{label} directory")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != required_mode
            or not 0 < before.st_size <= maximum_bytes
        ):
            raise OfflineRuntimeError(
                f"{label} must be one owner-controlled {required_mode:04o} regular file"
            )
        value = _read_descriptor(descriptor, size=before.st_size, label=label)
        after = os.fstat(descriptor)
        linked = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if _identity(before) != _identity(after) or _identity(after) != _identity(
            linked
        ):
            raise OfflineRuntimeError(f"{label} changed while it was read")
        return value
    except OSError as exc:
        raise OfflineRuntimeError(f"{label} is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def read_checkout_file(
    path: Path, *, label: str, maximum_bytes: int = MAX_FILE_BYTES
) -> bytes:
    reject_symlink_components(path.parent, label=f"{label} directory")
    parent = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0),
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 0 < before.st_size <= maximum_bytes
        ):
            raise OfflineRuntimeError(f"{label} is not a stable reviewed file")
        value = _read_descriptor(descriptor, size=before.st_size, label=label)
        after = os.fstat(descriptor)
        linked = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if _identity(before) != _identity(after) or _identity(after) != _identity(
            linked
        ):
            raise OfflineRuntimeError(f"{label} changed while it was read")
        return value
    except OSError as exc:
        raise OfflineRuntimeError(f"{label} could not be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _resolve_executable_source(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise OfflineRuntimeError(f"the {label} executable is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise OfflineRuntimeError(f"the {label} executable is not trusted")
    return resolved


def executable_sha256(path: Path, *, label: str) -> str:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o111:
            raise OfflineRuntimeError(f"the {label} executable is invalid")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise OfflineRuntimeError(f"the {label} executable changed while hashed")
        return digest.hexdigest()
    except OSError as exc:
        raise OfflineRuntimeError(
            f"the {label} executable could not be hashed"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def verify_private_executable(path: Path, *, expected: str, label: str) -> str:
    parent = open_private_directory(path.parent, label=f"private {label} directory")
    os.close(parent)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise OfflineRuntimeError(
            f"the private {label} executable is unavailable"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o500
    ):
        raise OfflineRuntimeError(f"the private {label} executable is not exact")
    observed = executable_sha256(path, label=label)
    if observed != expected:
        raise OfflineRuntimeError(f"the {label} executable digest is not pinned")
    return observed


def copy_pinned_executable(
    source: Path, destination: Path, *, expected: str, label: str
) -> Path:
    if len(expected) != 64 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise OfflineRuntimeError(f"the {label} expected digest is invalid")
    source = _resolve_executable_source(source, label=label)
    parent = open_private_directory(
        destination.parent, label=f"private {label} directory"
    )
    source_fd: int | None = None
    destination_fd: int | None = None
    created = False
    complete = False
    try:
        source_fd = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        source_before = os.fstat(source_fd)
        destination_fd = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o500,
            dir_fd=parent,
        )
        created = True
        digest = hashlib.sha256()
        while chunk := os.read(source_fd, 1024 * 1024):
            digest.update(chunk)
            offset = 0
            while offset < len(chunk):
                offset += os.write(destination_fd, chunk[offset:])
        os.fsync(destination_fd)
        os.fchmod(destination_fd, 0o500)
        if _identity(source_before) != _identity(os.fstat(source_fd)):
            raise OfflineRuntimeError(f"the {label} source changed while copied")
        if digest.hexdigest() != expected:
            raise OfflineRuntimeError(f"the {label} executable digest is not pinned")
        complete = True
    except OSError as exc:
        raise OfflineRuntimeError(
            f"the {label} executable could not be isolated"
        ) from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)
        if created and not complete:
            try:
                os.unlink(destination.name, dir_fd=parent)
            except OSError:
                pass
        os.close(parent)
    verify_private_executable(destination, expected=expected, label=label)
    return destination


def materialize_source(directory: Path, files: Mapping[str, bytes]) -> None:
    if not files or any(
        Path(name).name != name or not value for name, value in files.items()
    ):
        raise OfflineRuntimeError("decoder source member is invalid")
    reject_symlink_components(
        directory, label="decoder source", allow_missing_leaf=True
    )
    parent = open_private_directory(directory.parent, label="decoder source parent")
    temporary: Path | None = None
    target: int | None = None
    published = False
    try:
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{directory.name}-", dir=directory.parent)
        )
        temporary.chmod(0o700)
        target = open_private_directory(temporary, label="temporary decoder source")
        for name, value in files.items():
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o400,
                dir_fd=target,
            )
            try:
                offset = 0
                while offset < len(value):
                    offset += os.write(descriptor, value[offset:])
                os.fsync(descriptor)
                os.fchmod(descriptor, 0o400)
            finally:
                os.close(descriptor)
        os.fsync(target)
        os.close(target)
        target = None
        os.rename(temporary.name, directory.name, src_dir_fd=parent, dst_dir_fd=parent)
        published = True
        temporary = None
        os.fsync(parent)
    except (OSError, OfflineRuntimeError) as exc:
        if published:
            shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, OfflineRuntimeError):
            raise
        raise OfflineRuntimeError("decoder source could not be materialized") from exc
    finally:
        if target is not None:
            os.close(target)
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        os.close(parent)


def harden_private_tree(root: Path, *, executable: Path) -> None:
    """Make an already-private extracted package immutable to other principals."""
    require_private_directory(root, label="private runtime root")
    expected_executable = root / executable
    seen_executable = False
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in (*names, *files):
            path = directory_path / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise OfflineRuntimeError(
                    "private runtime tree contains an unsafe entry"
                )
            if stat.S_ISDIR(metadata.st_mode):
                path.chmod(0o700)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                is_executable = path == expected_executable
                path.chmod(0o500 if is_executable else 0o400)
                seen_executable |= is_executable
            else:
                raise OfflineRuntimeError(
                    "private runtime tree contains an unsafe entry"
                )
    if not seen_executable:
        raise OfflineRuntimeError("private runtime executable is missing")
