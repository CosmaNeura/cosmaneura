"""Read-only PostgreSQL16 client/private-library binding for the human snapshot.

No process, database, network, credential, source copying or permission change.
Only the fixed recorded clients/private libraries, public CA and protected
OS build are accepted. The caller must use the separately reviewed full source
bootstrap and actual plan/handoff gates. This is not a hostile-host sandbox.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import signal
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from staging_snapshot_native_files import NativeFiles, identity, owned_open
from staging_snapshot_pg_io import (
    CLIENT_LIBRARIES,
    capture_deadline,
    require_capture_process,
)

BREW = Path("/opt/homebrew")
# Recorded complete non-system Mach-O dependency closure, not caller-selected
# hashes. See flight plan for the original read-only inventory and its digest.
# Relative path, exact bytes, exact mode, raw SHA256.
FILE_PINS = (
    (
        "Cellar/gettext/1.0/lib/libintl.8.dylib",
        212080,
        0o444,
        "c84dabdb4ece983eded000a677040666c8496cd55c07db9816bf4a3eb216da58",
    ),
    (
        "Cellar/krb5/1.22.2/lib/libcom_err.3.0.dylib",
        53024,
        0o444,
        "981358042319a80778f86ff4aaffbe3e1413b151aa938bbef383d19873824864",
    ),
    (
        "Cellar/krb5/1.22.2/lib/libgssapi_krb5.2.2.dylib",
        308176,
        0o444,
        "bf1e5a0bf2865891e46da755ab96375f4c63f1c66752565003cbb64f33844a14",
    ),
    (
        "Cellar/krb5/1.22.2/lib/libk5crypto.3.1.dylib",
        179680,
        0o444,
        "694fda297c2cb76a21be41d4c8390090047742055a4c74835c024a157232ab89",
    ),
    (
        "Cellar/krb5/1.22.2/lib/libkrb5.3.3.dylib",
        774256,
        0o444,
        "72d8b1ee662c617a3a2a96c68e46ef653b9fe467dab95d2abeef1d40fd109936",
    ),
    (
        "Cellar/krb5/1.22.2/lib/libkrb5support.1.1.dylib",
        78032,
        0o444,
        "97da2a50b35566fd887ef47d56a77a374da182fb23f7312f44dad17c01b851cb",
    ),
    (
        "Cellar/lz4/1.10.0/lib/liblz4.1.10.0.dylib",
        160944,
        0o444,
        "eb5734d2edaf7e9aa64d03d0add9a9ac3656830d7acb14ff69a43b1d96ead0f5",
    ),
    (
        "Cellar/openssl@3/3.6.3/lib/libcrypto.3.dylib",
        4856256,
        0o444,
        "a12805a18cd5e4f733fa8727b91afa08b587f9da5a760517cd79cb508a3a3f71",
    ),
    (
        "Cellar/openssl@3/3.6.3/lib/libssl.3.dylib",
        872080,
        0o444,
        "ffd8ac6981000def0928367924b6cb1e7a98712efbc06e2a2f3f750138bd89ca",
    ),
    (
        "Cellar/postgresql@16/16.15/bin/pg_dump",
        444064,
        0o555,
        "7cfa7424e38d30cf8457fbeb6bbaa4c50ae7a24f8f00af4ad9118ee10220e755",
    ),
    (
        "Cellar/postgresql@16/16.15/bin/pg_restore",
        254016,
        0o555,
        "b5dd3ab3d4fa29f71e272ac08c805e0e4e995f87c7864368193d9b9a5a10f534",
    ),
    (
        "Cellar/postgresql@16/16.15/bin/psql",
        713904,
        0o555,
        "e528355f85a1e685618ec6e69ccdee666d2e1a48978d20e2e770e16bb94b19ad",
    ),
    (
        "Cellar/postgresql@16/16.15/lib/libpq.5.dylib",
        319088,
        0o444,
        "25714264fd8079982bfa0e055afab2ad9f1e506aada3c0466956c0145d3bd792",
    ),
    (
        "Cellar/readline/8.3.3/lib/libreadline.8.3.dylib",
        298000,
        0o444,
        "4d1a6fd9c9d005f52b27fa5f80e8af8538a53975c132c0e558774efd0fa2c2cf",
    ),
    (
        "Cellar/zstd/1.5.7_1/lib/libzstd.1.5.7.dylib",
        635328,
        0o444,
        "602d50cbe6fad0f0da6d1b73284ae3f75316015aea482ebd55614b6df2406b43",
    ),
)
LOAD_ROUTES = (
    ("opt/gettext/lib/libintl.8.dylib", "Cellar/gettext/1.0/lib/libintl.8.dylib"),
    *(
        ("opt/krb5/lib/" + name, "Cellar/krb5/1.22.2/lib/" + name)
        for name in (
            "libcom_err.3.0.dylib",
            "libgssapi_krb5.2.2.dylib",
            "libk5crypto.3.1.dylib",
            "libkrb5.3.3.dylib",
            "libkrb5support.1.1.dylib",
        )
    ),
    ("opt/lz4/lib/liblz4.1.dylib", "Cellar/lz4/1.10.0/lib/liblz4.1.10.0.dylib"),
    (
        "opt/openssl@3/lib/libcrypto.3.dylib",
        "Cellar/openssl@3/3.6.3/lib/libcrypto.3.dylib",
    ),
    ("opt/openssl@3/lib/libssl.3.dylib", "Cellar/openssl@3/3.6.3/lib/libssl.3.dylib"),
    (
        "opt/postgresql@16/lib/libpq.5.dylib",
        "Cellar/postgresql@16/16.15/lib/libpq.5.dylib",
    ),
    (
        "opt/readline/lib/libreadline.8.dylib",
        "Cellar/readline/8.3.3/lib/libreadline.8.3.dylib",
    ),
    ("opt/zstd/lib/libzstd.1.dylib", "Cellar/zstd/1.5.7_1/lib/libzstd.1.5.7.dylib"),
)
CA = Path("/private/etc/ssl/cert.pem")
CA_SHA256 = "9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994"
BUILD = Path("/System/Library/CoreServices/SystemVersion.plist")
EXPECTED_BUILD = "25G83"


class PostgreSQLRuntimeRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise PostgreSQLRuntimeRefused("snapshot_postgresql_runtime_refused")


@dataclass(frozen=True)
class PostgreSQLObservation:
    client_and_library_files: int
    load_routes: int
    retained_paths: int
    os_build: str
    not_execution_authority: bool = True


class PostgreSQLRuntime:
    __slots__ = ("_files", "_attempted", "_metadata", "_observation")

    def __init__(self, files: NativeFiles):
        require_capture_process()
        require(type(files) is NativeFiles)
        self._files, self._attempted = files, False
        self._metadata: tuple[tuple[Path, tuple[int, ...], str | None], ...] = ()
        self._observation: PostgreSQLObservation | None = None

    def _remember(self, records: dict, path: Path, value: os.stat_result, target=None):
        require(len(records) <= 512 and path.is_absolute())
        record = (identity(value), target)
        if path in records:
            require(records[path] == record)
        records[path] = record

    def _file(
        self,
        records: dict,
        path: Path,
        *,
        mode: int,
        maximum: int,
        owner: int,
        expected: str | None,
        size: int | None = None,
    ) -> bytes:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        with self._files.protected_parent(path) as parent:
            with owned_open(path.name, flags, dir_fd=parent) as fd:
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
                require(before.st_uid == owner and stat.S_IMODE(before.st_mode) == mode)
                require(0 < before.st_size <= maximum)
                require(size is None or before.st_size == size)
                self._files.acl_safe(fd)
                chunks, remaining = [], before.st_size
                while remaining:
                    chunk = os.read(fd, min(65536, remaining))
                    require(chunk)
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                require(not os.read(fd, 1))
                require(expected is None or hashlib.sha256(raw).hexdigest() == expected)
                self._files.acl_safe(fd)
                require(
                    identity(before)
                    == identity(os.fstat(fd))
                    == identity(
                        os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                    )
                )
                self._remember(records, path, before)
                return raw

    def _client_parents(self, records: dict, path: Path) -> None:
        require(path.is_relative_to(BREW))
        for parent in path.parents:
            if not parent.is_relative_to(BREW):
                break
            with self._files.protected_parent(parent / ".closure-binding") as fd:
                self._remember(records, parent, os.fstat(fd))

    def _libraries(self, records: dict) -> None:
        """One closed flat namespace, not Homebrew's writable opt shortcuts."""
        canonical = {Path(path).name for path, _, mode, _ in FILE_PINS if mode == 0o444}
        aliases = {
            Path(source).name: Path(target).name
            for source, target in LOAD_ROUTES
            if Path(source).name != Path(target).name
        }
        require(not canonical.intersection(aliases))
        require(set(aliases.values()) <= canonical)
        with self._files.protected_parent(CLIENT_LIBRARIES / ".closure-binding") as fd:
            before = os.fstat(fd)
            require(before.st_uid == os.geteuid())
            require(stat.S_IMODE(before.st_mode) == 0o500)
            self._files.acl_safe(fd)
            require(set(os.listdir(fd)) == canonical | set(aliases))
            for name, target in aliases.items():
                value = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(stat.S_ISLNK(value.st_mode) and value.st_nlink == 1)
                require(value.st_uid == os.geteuid())
                require(os.readlink(name, dir_fd=fd) == target)
                require(
                    identity(value)
                    == identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                )
                self._remember(records, CLIENT_LIBRARIES / name, value, target)
            self._files.acl_safe(fd)
            require(identity(before) == identity(os.fstat(fd)))
            self._remember(records, CLIENT_LIBRARIES, before)

    def _check(self, records: tuple) -> None:
        for path, expected, target in records:
            if stat.S_ISDIR(expected[4]):
                with self._files.protected_parent(path / ".closure-binding") as fd:
                    require(identity(os.fstat(fd)) == expected)
                continue
            with self._files.protected_parent(path) as parent:
                current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                require(identity(current) == expected)
                if target is not None:
                    require(os.readlink(path.name, dir_fd=parent) == target)
                require(
                    identity(current)
                    == identity(
                        os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                    )
                )

    def bind(self, *, deadline: float) -> PostgreSQLObservation:
        try:
            require_capture_process()
            require(not self._attempted and type(self._files) is NativeFiles)
            self._attempted = True
            require(sys.platform == "darwin" and os.uname().machine == "arm64")
            with capture_deadline(deadline):
                records: dict = {}
                build = self._file(
                    records, BUILD, owner=0, mode=0o444, maximum=65536, expected=None
                )
                require(
                    plistlib.loads(build).get("ProductBuildVersion") == EXPECTED_BUILD
                )
                self._file(
                    records,
                    CA,
                    owner=0,
                    mode=0o644,
                    maximum=4 * 1024**2,
                    size=333483,
                    expected=CA_SHA256,
                )
                self._libraries(records)
                for relative, size, mode, sha in FILE_PINS:
                    require(0 < size <= 16 * 1024**2)
                    require(mode in {0o444, 0o555})
                    path = (
                        BREW / relative
                        if mode == 0o555
                        else CLIENT_LIBRARIES / Path(relative).name
                    )
                    self._file(
                        records,
                        path,
                        owner=os.geteuid(),
                        mode=0o555 if mode == 0o555 else 0o400,
                        maximum=16 * 1024**2,
                        size=size,
                        expected=sha,
                    )
                    if mode == 0o555:
                        self._client_parents(records, path)
                retained = tuple(
                    (path, *record) for path, record in sorted(records.items())
                )
                self._check(retained)
                observation = PostgreSQLObservation(
                    len(FILE_PINS), len(LOAD_ROUTES), len(retained), EXPECTED_BUILD
                )
            self._metadata, self._observation = retained, observation
            return observation
        except BaseException:
            raise PostgreSQLRuntimeRefused(
                "snapshot_postgresql_runtime_refused"
            ) from None

    def revalidate(self) -> None:
        try:
            require_capture_process()
            require(
                type(self._files) is NativeFiles
                and self._metadata
                and self._observation is not None
            )
            require(signal.getitimer(signal.ITIMER_REAL)[0] > 0)
            self._check(self._metadata)
        except BaseException:
            raise PostgreSQLRuntimeRefused(
                "snapshot_postgresql_runtime_refused"
            ) from None


if __name__ == "__main__":
    raise SystemExit("snapshot_postgresql_runtime_requires_reviewed_human_bootstrap")
