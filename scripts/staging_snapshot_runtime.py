"""Read-only binding of the existing immutable, relocated Azure CLI closure.

No runtime copying, chmod, subprocess, cache read, token, network or entrypoint.
The full manifest is hashed once before interaction. Every subsequent owned
child must check the retained complete inode/ctime/namespace binding before and
after execution under its active deadline. This is not a hostile-host sandbox,
source provenance, plan acceptance, or permission to execute a human consumer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
from dataclasses import dataclass
from pathlib import Path

from staging_snapshot_native_files import NativeFiles, identity, owned_open
from staging_snapshot_pg_io import capture_deadline, require_capture_process


class SnapshotRuntimeRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise SnapshotRuntimeRefused("snapshot_runtime_refused")


def formulae() -> tuple[tuple[str, str], ...]:
    # Same complete non-system closure as the normally merged validator.
    # No caller/plan-selected formula, version, library directory or executable.
    return (
        ("azure-cli", "2.89.1"),
        ("ca-certificates", "2026-08-13"),
        ("libsodium", "1.0.22"),
        ("libyaml", "0.2.5"),
        ("lz4", "1.10.0"),
        ("mpdecimal", "4.0.1"),
        ("openssl@3", "3.6.3"),
        ("python@3.14", "3.14.7"),
        ("readline", "8.3.3"),
        ("sqlite", "3.53.4"),
        ("xz", "5.8.3"),
        ("zstd", "1.5.7_1"),
    )


def manifest_sha256(entries: dict[str, tuple]) -> str:
    # Byte-for-byte compatible with the existing validator's manifest format.
    raw = json.dumps(
        [[name, *entries[name]] for name in sorted(entries)],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class RuntimeObservation:
    manifest_sha256: str
    entries: int
    bytes_hashed: int
    not_execution_authority: bool = True


class SnapshotRuntime:
    """Only a previously prepared immutable homebrew snapshot is accepted.

    expected_snapshot_sha256 MUST come from the independently reviewed exact
    source-derived preparation/plan, not a digest calculated from an untrusted
    runtime and then passed back as its own authority. The protected loader
    must verify source provenance and exact module/class identity separately.
    """

    __slots__ = ("_files", "_root", "_expected", "_metadata", "_observation")

    def __init__(self, files: NativeFiles, root: Path, expected_snapshot_sha256: str):
        require_capture_process()
        require(type(files) is NativeFiles)
        require(
            root.is_absolute()
            and root == Path(os.path.abspath(root))
            and root.name == "homebrew"
            and re.fullmatch(r"[0-9a-f]{64}", expected_snapshot_sha256)
        )
        self._files, self._root, self._expected = files, root, expected_snapshot_sha256
        self._metadata: tuple[tuple[str, tuple[int, ...]], ...] = ()
        self._observation: RuntimeObservation | None = None

    @property
    def python(self) -> Path:
        return self._root / (
            "Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/"
            "bin/python3.14"
        )

    @property
    def site_packages(self) -> Path:
        return (
            self._root / "Cellar/azure-cli/2.89.1/libexec/lib/python3.14/site-packages"
        )

    def _scan(self) -> tuple[dict[str, tuple], dict[str, tuple[int, ...]], int]:
        entries: dict[str, tuple] = {}
        metadata: dict[str, tuple[int, ...]] = {}
        total_bytes = 0
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK

        def remember(relative: str, value: os.stat_result) -> None:
            require(relative not in metadata and len(metadata) < 50_064)
            require(value.st_uid == os.geteuid())
            metadata[relative] = identity(value)

        def directory(parent: int, name: str, relative: str, *, include: bool):
            with owned_open(name, directory_flags, dir_fd=parent) as descriptor:
                before = os.fstat(descriptor)
                require(stat.S_ISDIR(before.st_mode))
                require(stat.S_IMODE(before.st_mode) == 0o500)
                self._files.acl_safe(descriptor)
                remember(relative, before)
                if include:
                    entries[relative] = ("directory",)
                names = sorted(os.listdir(descriptor))
                require(len(names) <= 50_000 and len(Path(relative).parts) <= 64)
                if relative == "Cellar":
                    require(names == sorted(name for name, _ in formulae()))
                elif relative.startswith("Cellar/") and len(Path(relative).parts) == 2:
                    require(names == [dict(formulae())[name]])
                elif relative == "opt":
                    require(names == sorted(name for name, _ in formulae()))
                for child in names:
                    child_relative = relative + "/" + child
                    child_stat = os.stat(
                        child, dir_fd=descriptor, follow_symlinks=False
                    )
                    if stat.S_ISDIR(child_stat.st_mode):
                        require(relative != "opt")
                        directory(
                            descriptor,
                            child,
                            child_relative,
                            include=len(Path(child_relative).parts) >= 3,
                        )
                    elif stat.S_ISLNK(child_stat.st_mode):
                        require(
                            relative
                            not in {"Cellar", *("Cellar/" + f for f, _ in formulae())}
                        )
                        target = os.readlink(child, dir_fd=descriptor)
                        require(not Path(target).is_absolute())
                        candidate = self._root / child_relative
                        require(
                            Path(
                                os.path.abspath(candidate.parent / target)
                            ).is_relative_to(self._root)
                            and candidate.resolve(strict=True).is_relative_to(
                                self._root
                            )
                        )
                        if relative == "opt":
                            require(
                                target
                                == "../Cellar/" + child + "/" + dict(formulae())[child]
                            )
                        remember(child_relative, child_stat)
                        entries[child_relative] = ("link", target)
                        require(
                            identity(child_stat)
                            == identity(
                                os.stat(child, dir_fd=descriptor, follow_symlinks=False)
                            )
                        )
                    else:
                        require(
                            relative != "opt" and len(Path(child_relative).parts) >= 4
                        )
                        regular(descriptor, child, child_relative)
                self._files.acl_safe(descriptor)
                require(
                    identity(before)
                    == identity(os.fstat(descriptor))
                    == identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                )

        def regular(parent: int, name: str, relative: str):
            nonlocal total_bytes
            with owned_open(name, file_flags, dir_fd=parent) as descriptor:
                before = os.fstat(descriptor)
                mode = stat.S_IMODE(before.st_mode)
                require(
                    stat.S_ISREG(before.st_mode)
                    and before.st_nlink == 1
                    and mode in {0o400, 0o500}
                    and 0 <= before.st_size <= 128 * 1024**2
                )
                remember(relative, before)
                self._files.acl_safe(descriptor)
                total_bytes += before.st_size
                require(total_bytes <= 768 * 1024**2)
                digest = hashlib.sha256()
                remaining = before.st_size
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    require(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                require(not os.read(descriptor, 1))
                self._files.acl_safe(descriptor)
                require(
                    identity(before)
                    == identity(os.fstat(descriptor))
                    == identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                )
                entries[relative] = (
                    "file",
                    bool(mode & 0o111),
                    before.st_size,
                    digest.hexdigest(),
                )

        with self._files.protected_parent(self._root / ".binding") as root:
            before = os.fstat(root)
            require(stat.S_IMODE(before.st_mode) == 0o500)
            self._files.acl_safe(root)
            remember(".", before)
            require(sorted(os.listdir(root)) == ["Cellar", "opt"])
            directory(root, "Cellar", "Cellar", include=False)
            directory(root, "opt", "opt", include=False)
            require(identity(before) == identity(os.fstat(root)))
        require(len(entries) <= 50_000)
        return entries, metadata, total_bytes

    def bind(self, *, deadline: float) -> RuntimeObservation:
        try:
            require_capture_process()
            require(not self._metadata and self._observation is None)
            with capture_deadline(deadline):
                entries, metadata, count = self._scan()
                require(manifest_sha256(entries) == self._expected)
                require(
                    entries.get(self.python.relative_to(self._root).as_posix(), ())[:2]
                    == ("file", True)
                    and entries.get(
                        self.site_packages.relative_to(self._root).as_posix()
                    )
                    == ("directory",)
                )
                # Reject changes to early entries while later files were read.
                retained = tuple(sorted(metadata.items()))
                self._check_metadata(retained)
                observation = RuntimeObservation(self._expected, len(entries), count)
            self._metadata, self._observation = retained, observation
            return observation
        except BaseException:
            raise SnapshotRuntimeRefused("snapshot_runtime_refused") from None

    def _check_metadata(self, retained: tuple[tuple[str, tuple[int, ...]], ...]):
        with self._files.protected_parent(self._root / ".binding"):
            for relative, expected in retained:
                require(identity((self._root / relative).lstat()) == expected)

    def revalidate(self) -> None:
        """For BoundedChildren's pre/post checks, inside its one active timer.

        Compare every retained directory/link/file including ctime, not only
        executables or mtime. Namespace, ACL, chmod, replacement and in-place
        edits invalidate the binding. No lazy native loading or subprocess.
        Never represents atomic exclusion of hostile concurrent same-UID edits.
        """
        try:
            require_capture_process()
            require(self._metadata and self._observation is not None)
            require(signal.getitimer(signal.ITIMER_REAL)[0] > 0)
            self._check_metadata(self._metadata)
        except BaseException:
            raise SnapshotRuntimeRefused("snapshot_runtime_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_runtime_requires_reviewed_human_bootstrap")
