"""Owned read-only native mount queries for a later reviewed HUMAN session.

Never invoke native workspace operations from an agent. No image creation,
attach/detach, passphrase, firewall, data copy, cleanup or CLI entrypoint here.
The exact manifest/tool/source pins come from the protected reviewed plan;
this component does not generate authority or replace the human bootstrap.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import staging_encrypted_workspace as workspace
from staging_snapshot_identity import read_object
from staging_snapshot_native_files import NativeFiles, identity, owned_open
from staging_snapshot_pg_io import (
    BoundedChildren,
    capture_deadline,
    require_capture_process,
)
from staging_snapshot_runtime import SnapshotRuntime


class SnapshotWorkspaceRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise SnapshotWorkspaceRefused("snapshot_workspace_refused")


@dataclass(frozen=True)
class BoundArea:
    owner: SnapshotWorkspace = field(repr=False)
    mount: Path
    mount_fd: int = field(repr=False)
    payload_fd: int = field(repr=False)
    mount_device: int
    mount_inode: int
    payload_inode: int


class SnapshotWorkspace:
    __slots__ = (
        "_files",
        "_runtime",
        "_root",
        "_manifest_sha",
        "_source_sha",
        "_tool_pins",
    )

    def __init__(
        self,
        files: NativeFiles,
        runtime: SnapshotRuntime,
        root: Path,
        manifest_sha256: str,
        source_sha256: str,
        hdiutil_sha256: str,
        diskutil_sha256: str,
    ):
        require_capture_process()
        require(type(files) is NativeFiles and type(runtime) is SnapshotRuntime)
        require(
            root.parent == Path("/private/tmp")
            and re.fullmatch(workspace.PREFIX + r"[a-z0-9_]{8}", root.name)
        )
        require(
            all(
                re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (
                    manifest_sha256,
                    source_sha256,
                    hdiutil_sha256,
                    diskutil_sha256,
                )
            )
        )
        self._files, self._runtime, self._root = files, runtime, root
        self._manifest_sha, self._source_sha = manifest_sha256, source_sha256
        self._tool_pins = (
            ("/usr/bin/hdiutil", hdiutil_sha256),
            ("/usr/sbin/diskutil", diskutil_sha256),
        )

    def _runtime_check(self):
        self._runtime.revalidate()
        for name, expected in self._tool_pins:
            path = Path(name)
            with self._files.protected_parent(path) as parent:
                with owned_open(
                    path.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                    dir_fd=parent,
                ) as descriptor:
                    before = os.fstat(descriptor)
                    require(
                        stat.S_ISREG(before.st_mode)
                        and before.st_uid == 0
                        and before.st_nlink == 1
                        and not stat.S_IMODE(before.st_mode) & 0o022
                        and stat.S_IMODE(before.st_mode) & 0o111
                        and 0 < before.st_size <= 32 * 1024**2
                    )
                    self._files.acl_safe(descriptor)
                    digest = hashlib.sha256()
                    remaining = before.st_size
                    while remaining:
                        chunk = os.read(descriptor, min(65536, remaining))
                        require(chunk)
                        remaining -= len(chunk)
                        digest.update(chunk)
                    require(digest.hexdigest() == expected)
                    self._files.acl_safe(descriptor)
                    require(
                        identity(before)
                        == identity(os.fstat(descriptor))
                        == identity(
                            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                        )
                    )

    def _query(self, operation: str, deadline: float) -> dict:
        _image, mount = workspace.workspace_paths(self._root)
        if operation == "images":
            argv = ["/usr/bin/hdiutil", "info", "-plist"]
        elif operation == "disk":
            argv = ["/usr/sbin/diskutil", "info", "-plist", str(mount)]
        else:
            raise SnapshotWorkspaceRefused("snapshot_workspace_refused")
        output = bytearray()
        children = BoundedChildren(
            min(deadline, time.monotonic() + 30), self._runtime_check
        )
        children.run(
            argv,
            {"PATH": "/usr/bin:/bin:/usr/sbin", "LC_ALL": "C", "LANG": "C"},
            2 * 1024**2,
            output.extend,
        )
        self._runtime_check()
        return workspace.parse_plist(bytes(output))

    def _observe(self, deadline: float) -> dict:
        require_capture_process()
        require(
            signal.getitimer(signal.ITIMER_REAL)[0] > 0 and time.monotonic() < deadline
        )
        image, mount = workspace.workspace_paths(self._root)
        manifest = self._files.read(
            self._root / workspace.MANIFEST_NAME, mode=0o400, maximum=32768
        )
        require(hashlib.sha256(manifest.content).hexdigest() == self._manifest_sha)
        value = read_object(manifest.content, 32768)
        require(
            value.get("schema_version") == 1
            and value.get("empty_workspace_prepared") is True
            and value.get("source_sha256") == self._source_sha
            and value.get("requested_encryption") == "AES-256"
            and value.get("workspace") == str(self._root)
            and value.get("image") == str(image)
            and value.get("mount") == str(mount)
            and value.get("capacity_bytes") == workspace.SIZE_BYTES
        )
        image_before = workspace.private_path(image, directory=False, mode=0o600)
        mount_before = workspace.private_path(mount, directory=True, mode=0o700)
        require(
            mount_before.st_dev != self._root.stat().st_dev and os.path.ismount(mount)
        )
        # Native image_binding proves encryption on the exact attached image;
        # opening its backing file with isencrypted is unavailable while mounted.
        device = workspace.image_binding(self._query("images", deadline), image, mount)
        workspace.check_disk(self._query("disk", deadline), mount, device)
        image_after = workspace.private_path(image, directory=False, mode=0o600)
        mount_after = workspace.private_path(mount, directory=True, mode=0o700)
        require(
            (image_before.st_dev, image_before.st_ino)
            == (image_after.st_dev, image_after.st_ino)
            == (value.get("image_device"), value.get("image_inode"))
        )
        require(
            (mount_before.st_dev, mount_before.st_ino)
            == (mount_after.st_dev, mount_after.st_ino)
            == (value.get("mount_device"), value.get("mount_inode"))
            and device == value.get("device")
        )
        require(
            self._files.read(
                self._root / workspace.MANIFEST_NAME, mode=0o400, maximum=32768
            )
            == manifest
        )
        return value

    def revalidate(self, area: BoundArea, *, deadline: float) -> dict:
        """Inside caller's existing timer; never start/nest or extend that timer."""
        try:
            require(type(area) is BoundArea and area.owner is self)
            observed = self._observe(deadline)
            for fd, inode, path in (
                (area.mount_fd, area.mount_inode, area.mount),
                (
                    area.payload_fd,
                    area.payload_inode,
                    area.mount / workspace.PAYLOAD_NAME,
                ),
            ):
                metadata = os.fstat(fd)
                self._files.acl_safe(fd)
                require(
                    stat.S_ISDIR(metadata.st_mode)
                    and metadata.st_uid == os.geteuid()
                    and stat.S_IMODE(metadata.st_mode) == 0o700
                    and metadata.st_dev == area.mount_device
                    and metadata.st_ino == inode
                    and identity(metadata) == identity(path.lstat())
                )
            require(
                observed["mount_device"] == area.mount_device
                and observed["mount_inode"] == area.mount_inode
                and observed["payload_inode"] == area.payload_inode
                and observed["payload_device"] == area.mount_device
            )
            marker_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            with owned_open(
                workspace.MARKER_NAME, marker_flags, dir_fd=area.mount_fd
            ) as marker:
                value = os.fstat(marker)
                self._files.acl_safe(marker)
                require(
                    stat.S_ISREG(value.st_mode)
                    and value.st_uid == os.geteuid()
                    and stat.S_IMODE(value.st_mode) == 0o600
                    and value.st_nlink == 1
                    and value.st_size == 0
                    and value.st_dev == area.mount_device
                    and value.st_ino == observed["noindex_marker_inode"]
                )
            return observed
        except BaseException:
            raise SnapshotWorkspaceRefused("snapshot_workspace_refused") from None

    @contextmanager
    def open_area(self, *, deadline: float) -> Iterator[BoundArea]:
        """Retain native-bound mount/payload fds; return with NO active timer.

        Configuration lives alongside payload, never inside its empty archive
        directory. No directory/file is created here and no data is removed.
        """
        try:
            require_capture_process()
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            with self._files.protected_parent(
                self._root / workspace.MANIFEST_NAME
            ) as root:
                with owned_open(workspace.MOUNT_NAME, flags, dir_fd=root) as mount:
                    with owned_open(
                        workspace.PAYLOAD_NAME, flags, dir_fd=mount
                    ) as payload:
                        metadata, payload_metadata = os.fstat(mount), os.fstat(payload)
                        area = BoundArea(
                            self,
                            self._root / workspace.MOUNT_NAME,
                            mount,
                            payload,
                            metadata.st_dev,
                            metadata.st_ino,
                            payload_metadata.st_ino,
                        )
                        with capture_deadline(deadline):
                            self.revalidate(area, deadline=deadline)
                            require(not os.listdir(payload))
                        yield area
        except BaseException:
            raise SnapshotWorkspaceRefused("snapshot_workspace_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_workspace_requires_reviewed_human_bootstrap")
