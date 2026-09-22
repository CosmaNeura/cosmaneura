"""Configured-user cache binding and a private copy ONLY on a bound image.

HUMAN-session library, no CLI/entrypoint. Never run against real credentials
from an agent. Original configuration is read-only; copies/partial copies are
preserved on the verified encrypted image, never written back or auto-deleted.
The protected human loader must attest this exact code and all supplied pins.
"""

from __future__ import annotations

import hashlib
import os
import pwd
import signal
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from staging_snapshot_native_files import FileObservation, NativeFiles
from staging_snapshot_pg_io import (
    capture_deadline,
    defer_control_signals,
    require_capture_process,
)
from staging_snapshot_workspace import BoundArea, SnapshotWorkspace


class SnapshotConfigurationRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise SnapshotConfigurationRefused("snapshot_configuration_refused")


def names() -> tuple[str, ...]:
    return "config", "clouds.config", "azureProfile.json", "msal_token_cache.json"


def private_umask() -> None:
    with defer_control_signals():
        previous = os.umask(0o077)
        os.umask(previous)
    require(previous == 0o077)


@contextmanager
def private_new_file(parent: int, name: str) -> Iterator[int]:
    descriptor = None
    try:
        with defer_control_signals():
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent,
            )
        yield descriptor
    finally:
        if descriptor is not None:
            with defer_control_signals():
                os.close(descriptor)


@dataclass(frozen=True)
class BoundConfiguration:
    owner: ConfiguredSource = field(repr=False)
    area: BoundArea = field(repr=False)
    path: Path
    directory_fd: int = field(repr=False)
    extension_fd: int = field(repr=False)
    directory_inode: int
    extension_inode: int


class ConfiguredSource:
    __slots__ = ("_files", "_home", "_original")

    def __init__(self, files: NativeFiles, *, deadline: float):
        try:
            require_capture_process()
            require(type(files) is NativeFiles)
            self._files = files
            self._home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
            require(
                self._home.is_absolute()
                and self._home == self._home.resolve(strict=True)
            )
            with capture_deadline(deadline):
                self._original = self._read()
                require(
                    hashlib.sha256(self._original[0].content).hexdigest()
                    == "b8258f4205b85a305ac3811432073f56fddf4a63b4472485778bfde0f5a69553"
                )
                require(
                    hashlib.sha256(self._original[1].content).hexdigest()
                    == "2189f3b4fe7357a59f5fcdf8f6e35382784aa2862b5262e5dc89d8e615bfc3ed"
                )
        except BaseException:
            raise SnapshotConfigurationRefused(
                "snapshot_configuration_refused"
            ) from None

    def _read(self) -> tuple[FileObservation, ...]:
        require_capture_process()
        require(type(self._files) is NativeFiles)
        require(signal.getitimer(signal.ITIMER_REAL)[0] > 0)
        require(Path(pwd.getpwuid(os.geteuid()).pw_dir) == self._home)
        result = []
        for name in names():
            path = self._home / ".azure" / name
            mode = stat.S_IMODE(path.lstat().st_mode)
            require(mode in {0o400, 0o600})
            result.append(self._files.read(path, mode=mode, maximum=4 * 1024**2))
        return tuple(result)

    def revalidate(self) -> None:
        try:
            require(self._read() == self._original)
        except BaseException:
            raise SnapshotConfigurationRefused(
                "snapshot_configuration_refused"
            ) from None

    def identity_cache(self) -> bytes:
        # Only for the exact-token/MSAL linkage verifier in the human session;
        # never an alternative to the normal CLI's configured-account read.
        self.revalidate()
        return self._original[3].content

    def validate_copy(self, copy: BoundConfiguration, *, deadline: float) -> None:
        try:
            require_capture_process()
            require(type(copy) is BoundConfiguration and copy.owner is self)
            require(
                type(copy.area) is BoundArea
                and type(copy.area.owner) is SnapshotWorkspace
            )
            private_umask()
            require(time.monotonic() < deadline)
            self.revalidate()
            copy.area.owner.revalidate(copy.area, deadline=deadline)
            for descriptor, inode, path in (
                (copy.directory_fd, copy.directory_inode, copy.path),
                (
                    copy.extension_fd,
                    copy.extension_inode,
                    copy.path / "empty-extensions",
                ),
            ):
                current = os.fstat(descriptor)
                linked = path.lstat()
                self._files.acl_safe(descriptor)
                require(
                    stat.S_ISDIR(current.st_mode)
                    and stat.S_ISDIR(linked.st_mode)
                    and current.st_uid == linked.st_uid == os.geteuid()
                    and stat.S_IMODE(current.st_mode)
                    == stat.S_IMODE(linked.st_mode)
                    == 0o700
                    and current.st_dev == linked.st_dev == copy.area.mount_device
                    and current.st_ino == linked.st_ino == inode
                )
            require(not os.listdir(copy.extension_fd))
            for name, original in zip(names(), self._original, strict=True):
                observed = self._files.read(
                    copy.path / name, mode=0o600, maximum=4 * 1024**2
                )
                # Only the token-cache COPY may be rewritten by normal CLI
                # token access. Never accept changed cloud/config/account
                # selection in the copy as a newly trusted configuration.
                if name != "msal_token_cache.json":
                    require(observed.content == original.content)
        except BaseException:
            raise SnapshotConfigurationRefused(
                "snapshot_configuration_refused"
            ) from None

    @contextmanager
    def private_copy(
        self, area: BoundArea, *, deadline: float
    ) -> Iterator[BoundConfiguration]:
        directory = extension = None
        try:
            require_capture_process()
            require(type(area) is BoundArea and type(area.owner) is SnapshotWorkspace)
            with capture_deadline(deadline):
                private_umask()
                self.revalidate()
                area.owner.revalidate(area, deadline=deadline)
                name = ".azure-cli-session-" + os.urandom(16).hex()
                # Deliberately preserve even an interrupted empty/partial
                # directory on the encrypted image; never recursive cleanup.
                os.mkdir(name, mode=0o700, dir_fd=area.mount_fd)
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                with defer_control_signals():
                    directory = os.open(name, flags, dir_fd=area.mount_fd)
                current = os.fstat(directory)
                require(
                    current.st_dev == area.mount_device
                    and current.st_uid == os.geteuid()
                    and stat.S_IMODE(current.st_mode) == 0o700
                )
                self._files.acl_safe(directory)
                for filename, observation in zip(names(), self._original, strict=True):
                    # Register and mask each descriptor acquisition;
                    # data writes/fsync are interruptible under the shared timer.
                    with private_new_file(directory, filename) as descriptor:
                        self._files.acl_safe(descriptor)
                        data = memoryview(observation.content)
                        while data:
                            written = os.write(descriptor, data)
                            require(written > 0)
                            data = data[written:]
                        os.fsync(descriptor)
                os.mkdir("empty-extensions", mode=0o700, dir_fd=directory)
                with defer_control_signals():
                    extension = os.open("empty-extensions", flags, dir_fd=directory)
                copy = BoundConfiguration(
                    self,
                    area,
                    area.mount / name,
                    directory,
                    extension,
                    current.st_ino,
                    os.fstat(extension).st_ino,
                )
                os.fsync(directory)
                self.validate_copy(copy, deadline=deadline)
            yield copy
        except BaseException:
            raise SnapshotConfigurationRefused(
                "snapshot_configuration_refused"
            ) from None
        finally:
            failed = False
            with defer_control_signals():
                for descriptor in (extension, directory):
                    if descriptor is not None:
                        try:
                            os.close(descriptor)
                        except OSError:
                            failed = True
            require(not failed)


if __name__ == "__main__":
    raise SystemExit("snapshot_configuration_requires_reviewed_human_bootstrap")
