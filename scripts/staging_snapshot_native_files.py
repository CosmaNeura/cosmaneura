"""Preloaded native ACL and protected-file reads for a sealed human process.

No CLI, network, token discovery, write, snapshot deletion or live entrypoint.
Construct NativeFiles before process sealing, after core limits are zero. All
secret-bearing reads require the seal. No native symbol/library/errno helper is
resolved dynamically after sealing. This is not authority or a runtime manifest.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from staging_snapshot_pg_io import (
    defer_control_signals,
    require_capture_process,
    require_core_disabled,
)


class ProtectedReadRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise ProtectedReadRefused("snapshot_protected_read_refused")


def identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    # An unrelated sibling's creation must not invalidate a held ancestor.
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_mode,
    )


@dataclass(frozen=True)
class FileObservation:
    content: bytes = field(repr=False)
    file_identity: tuple[int, ...]
    parent_identity: tuple[int, ...]


@contextmanager
def owned_open(path, flags, *, dir_fd=None) -> Iterator[int]:
    descriptor = None
    try:
        with defer_control_signals():
            descriptor = os.open(path, flags, dir_fd=dir_fd)
        yield descriptor
    finally:
        if descriptor is not None:
            with defer_control_signals():
                os.close(descriptor)


class NativeFiles:
    __slots__ = (
        "_library",
        "_get_acl",
        "_get_entry",
        "_get_tag",
        "_free_acl",
        "_errno",
    )

    def __init__(self):
        require_core_disabled()
        require(sys.platform in {"darwin", "linux"})
        if sys.platform == "darwin":
            # Resolve the protected OS library and EVERY symbol before sealing.
            # Native errno pointer avoids ctypes.get/set_errno audit operations.
            self._library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            self._get_acl = self._library.acl_get_fd_np
            self._get_acl.argtypes = (ctypes.c_int, ctypes.c_int)
            self._get_acl.restype = ctypes.c_void_p
            self._get_entry = self._library.acl_get_entry
            self._get_entry.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )
            self._get_entry.restype = ctypes.c_int
            self._get_tag = self._library.acl_get_tag_type
            self._get_tag.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
            self._get_tag.restype = ctypes.c_int
            self._free_acl = self._library.acl_free
            self._free_acl.argtypes = (ctypes.c_void_p,)
            self._free_acl.restype = ctypes.c_int
            self._errno = getattr(self._library, "__error")
            self._errno.argtypes = ()
            self._errno.restype = ctypes.POINTER(ctypes.c_int)

    def acl_safe(self, descriptor: int, *, allow_deny_only: bool = False) -> None:
        require_capture_process()
        if sys.platform == "linux":
            names = set(os.listxattr(descriptor))
            require(
                not names
                & {
                    "system.posix_acl_access",
                    "system.posix_acl_default",
                    "system.nfs4_acl",
                }
            )
            return
        acl = None
        try:
            with defer_control_signals():
                self._errno()[0] = 0
                acl = self._get_acl(descriptor, 0x100)
                error = self._errno()[0]
            if not acl:
                require(error == errno.ENOENT)
                return
            entry = ctypes.c_void_p()
            selector = 0
            for _ in range(256):
                self._errno()[0] = 0
                result = self._get_entry(acl, selector, ctypes.byref(entry))
                error = self._errno()[0]
                if result == -1 and error == errno.EINVAL:
                    return
                require(result == 0 and entry)
                tag = ctypes.c_int()
                require(self._get_tag(entry, ctypes.byref(tag)) == 0)
                require(allow_deny_only and tag.value == 2)
                selector = -1
            raise ProtectedReadRefused("snapshot_protected_read_refused")
        finally:
            if acl:
                with defer_control_signals():
                    require(self._free_acl(acl) == 0)

    @contextmanager
    def protected_parent(self, path: Path) -> Iterator[int]:
        """Hold every no-follow ancestor until the file operation is complete.

        Root-owned sticky ancestors are allowed, never a public-writable leaf
        parent. Ancestor deny-only ACLs (including macOS home directories) are
        allowed; an allow ACL anywhere in the path refuses. Recheck descriptors
        against their parent-relative names, not a second unanchored traversal.
        """
        require_capture_process()
        require(
            path.is_absolute()
            and path == Path(os.path.abspath(path))
            and 2 <= len(path.parts) <= 64
        )
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        opened: list[tuple[int, str, tuple[int, ...]]] = []
        try:
            for part in (path.anchor, *path.parts[1:-1]):
                with defer_control_signals():
                    descriptor = os.open(
                        part, flags, dir_fd=opened[-1][0] if opened else None
                    )
                    # Record ownership before any validation or signal delivery.
                    opened.append((descriptor, part, ()))
                metadata = os.fstat(descriptor)
                require(stat.S_ISDIR(metadata.st_mode))
                require(metadata.st_uid in {0, os.geteuid()})
                writable = stat.S_IMODE(metadata.st_mode) & 0o022
                require(
                    not writable
                    or (metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX)
                )
                self.acl_safe(descriptor, allow_deny_only=True)
                opened[-1] = (descriptor, part, directory_identity(metadata))
            require(not stat.S_IMODE(os.fstat(opened[-1][0]).st_mode) & 0o022)
            yield opened[-1][0]
            for index, (descriptor, part, expected) in enumerate(opened):
                self.acl_safe(descriptor, allow_deny_only=True)
                linked = os.stat(
                    part,
                    dir_fd=opened[index - 1][0] if index else None,
                    follow_symlinks=False,
                )
                require(
                    expected
                    == directory_identity(os.fstat(descriptor))
                    == directory_identity(linked)
                )
        finally:
            # Short, bounded descriptor-only teardown. Never mask traversal,
            # content reads, or an entire time-limited validation operation.
            failed = False
            with defer_control_signals():
                while opened:
                    descriptor, _, _ = opened.pop()
                    try:
                        os.close(descriptor)
                    except OSError:
                        failed = True
            require(not failed)

    def read(self, path: Path, *, mode: int, maximum: int) -> FileObservation:
        try:
            require_capture_process()
            require(
                mode in {0o400, 0o600}
                and type(maximum) is int
                and 0 < maximum <= 8 * 1024 * 1024
            )
            file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            with self.protected_parent(path) as parent:
                parent_before = os.fstat(parent)
                require(
                    stat.S_ISDIR(parent_before.st_mode)
                    and parent_before.st_uid == os.geteuid()
                    and stat.S_IMODE(parent_before.st_mode) == 0o700
                )
                self.acl_safe(parent)
                with owned_open(path.name, file_flags, dir_fd=parent) as descriptor:
                    before = os.fstat(descriptor)
                    require(
                        stat.S_ISREG(before.st_mode)
                        and before.st_uid == os.geteuid()
                        and before.st_nlink == 1
                        and stat.S_IMODE(before.st_mode) == mode
                        and 0 < before.st_size <= maximum
                    )
                    self.acl_safe(descriptor)
                    chunks = []
                    remaining = before.st_size
                    while remaining:
                        chunk = os.read(descriptor, min(65536, remaining))
                        require(chunk)
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    value = b"".join(chunks)
                    # Same-size writes can share the original timestamps.
                    # Recheck the captured bytes through the same held file
                    # descriptor before accepting its final metadata.
                    require(os.lseek(descriptor, 0, os.SEEK_SET) == 0)
                    offset = 0
                    while offset < len(value):
                        chunk = os.read(descriptor, min(65536, len(value) - offset))
                        require(chunk and chunk == value[offset : offset + len(chunk)])
                        offset += len(chunk)
                    require(os.read(descriptor, 1) == b"")
                    self.acl_safe(descriptor)
                    self.acl_safe(parent)
                    require(
                        identity(before)
                        == identity(os.fstat(descriptor))
                        == identity(
                            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                        )
                    )
                    require(
                        identity(parent_before)
                        == identity(os.fstat(parent))
                        == identity(path.parent.lstat())
                    )
                    return FileObservation(
                        value, identity(before), identity(parent_before)
                    )
        except BaseException:
            raise ProtectedReadRefused("snapshot_protected_read_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_native_files_requires_reviewed_human_bootstrap")
