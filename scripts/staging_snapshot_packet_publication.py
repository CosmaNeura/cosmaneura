"""Exclusive PUBLIC snapshot packet publication, never human execution.

Only invented fixtures may publish while the operator is absent. Actual callers
owe authenticated original evidence, reviewed source and same-task presence;
hashes/dataclasses are consistency anchors, not self-authentication. No network,
credentials, human consumer, claim, receipt, overwrite, retry or cleanup here.
This trusted local helper is not a sandbox against malicious same-UID/root code.
"""

from __future__ import annotations

import ctypes
import errno
import os
import re
import signal
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import staging_snapshot_public_plan as plan
from staging_snapshot_review_record import ReviewRecord, record, sha, valid_sha
from staging_snapshot_source_provenance import SourceCollection

PACKET_PARENT = Path("/private/tmp/cosmaneura-staging-baseline-20260914.adNnmt")
PARTS = {
    "summary.json": 16384,
    "request.json": 500000,
    "response.json": 2 * 1024**2,
    "text.md": 128 * 1024,
}
CAPTURE = {
    "handoff.json": 32768,
    "plan.json": 65536,
    "identity.json": 16384,
    "source-provenance.json": 256 * 1024,
    **{
        f"{kind}-review-{part}": size
        for kind in ("source", "plan", "handoff")
        for part, size in PARTS.items()
    },
}
PREPARATION = {
    "preparation.json": 16384,
    **{name: size for name, size in CAPTURE.items() if name.startswith("source-")},
}


class PacketPublicationRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise PacketPublicationRefused("snapshot_packet_publication_refused")


@dataclass(frozen=True)
class PreparationPacket:
    files: tuple[tuple[str, bytes], ...]
    source: SourceCollection
    source_review: ReviewRecord
    workspace_manifest: bytes


@dataclass(frozen=True)
class Publication:
    """Local file observations ONLY, not a permanent readiness/acceptance record."""

    packet: Path
    directory_identity: tuple[int, ...]
    file_sha256: tuple[tuple[str, str], ...]
    file_identities: tuple[tuple[str, tuple[int, ...]], ...]
    observation_directory: Path | None
    observation_directory_identity: tuple[int, ...] | None


@contextmanager
def masked() -> Iterator[None]:
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGALRM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextmanager
def operation() -> Iterator[Callable[[], None]]:
    """One 30-second main-thread timer; refuse rather than steal a caller's alarm."""
    require(sys.platform in {"darwin", "linux"})
    require(threading.current_thread() is threading.main_thread())
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    require(signal.SIGALRM not in original_mask)
    require(signal.SIGALRM not in signal.sigpending())
    started, wall = time.monotonic(), plan.now_utc()
    handler = signal.getsignal(signal.SIGALRM)
    fired = [False]
    closing = False

    def tick() -> None:
        elapsed = time.monotonic() - started
        require(not fired[0] and 0 <= elapsed < 30)
        require(abs((plan.now_utc() - wall).total_seconds() - elapsed) <= 5)

    def expired(_signal, _frame):
        fired[0] = True
        if not closing:
            raise PacketPublicationRefused("snapshot_packet_publication_refused")

    try:
        with masked():
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, 30)
        tick()
        yield tick
        tick()
        require(signal.getsignal(signal.SIGALRM) is expired)
    finally:
        # Record expiry during teardown; never deliver our pending alarm to the
        # restored caller handler or interrupt restoration half way through.
        try:
            closing = True
        finally:
            closing = True
            try:
                signal.pthread_sigmask(
                    signal.SIG_BLOCK, {signal.SIGINT, signal.SIGALRM}
                )
            finally:
                try:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                finally:
                    try:
                        if signal.SIGALRM in signal.sigpending():
                            fired[0] = True
                            signal.sigwait({signal.SIGALRM})
                    finally:
                        try:
                            signal.signal(signal.SIGALRM, handler)
                        finally:
                            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        tick()


class ACL:
    """Read-only descriptor ACL inspection; no permission repair or child process."""

    def __init__(self):
        if sys.platform == "darwin":
            self.lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            self.get = self.lib.acl_get_fd_np
            self.get.argtypes, self.get.restype = (
                (ctypes.c_int, ctypes.c_int),
                ctypes.c_void_p,
            )
            self.entry = self.lib.acl_get_entry
            self.entry.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )
            self.entry.restype = ctypes.c_int
            self.tag = self.lib.acl_get_tag_type
            self.tag.argtypes, self.tag.restype = (
                (ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)),
                ctypes.c_int,
            )
            self.free = self.lib.acl_free
            self.free.argtypes, self.free.restype = (ctypes.c_void_p,), ctypes.c_int

    def check(self, fd: int, *, ancestor: bool = False) -> None:
        if sys.platform == "linux":
            require(
                not set(os.listxattr(fd))
                & {
                    "system.posix_acl_access",
                    "system.posix_acl_default",
                    "system.nfs4_acl",
                }
            )
            return
        acl = None
        try:
            with masked():
                ctypes.set_errno(0)
                acl = self.get(fd, 0x100)
                error = ctypes.get_errno()
            if not acl:
                require(error == errno.ENOENT)
                return
            entry, selector = ctypes.c_void_p(), 0
            for _ in range(256):
                ctypes.set_errno(0)
                result = self.entry(acl, selector, ctypes.byref(entry))
                if result == -1 and ctypes.get_errno() == errno.EINVAL:
                    return
                require(result == 0 and entry)
                tag = ctypes.c_int()
                require(self.tag(entry, ctypes.byref(tag)) == 0)
                require(ancestor and tag.value == 2)  # Deny-only ancestor ACL.
                selector = -1
            require(False)
        finally:
            if acl:
                with masked():
                    require(self.free(acl) == 0)


def directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode


def file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        *directory_identity(value),
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class Descriptors:
    def __init__(self):
        self.values: list[int] = []

    def open(self, name, flags, *, parent=None, mode=0o400):
        with masked():
            fd = os.open(name, flags, mode, dir_fd=parent)
            self.values.append(fd)
        return fd

    def close(self):
        failed = False
        with masked():
            while self.values:
                try:
                    os.close(self.values.pop())
                except OSError:
                    failed = True
        require(not failed)


@contextmanager
def descriptors() -> Iterator[Descriptors]:
    owned = Descriptors()
    try:
        yield owned
    finally:
        owned.close()


def private_directory(fd: int, acl: ACL) -> tuple[int, ...]:
    value = os.fstat(fd)
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.geteuid())
    require(stat.S_IMODE(value.st_mode) == 0o700)
    acl.check(fd)
    return directory_identity(value)


@contextmanager
def protected_parent(acl: ACL, tick: Callable[[], None]) -> Iterator[int]:
    path = PACKET_PARENT
    require(path.is_absolute() and path == Path(os.path.abspath(path)))
    require(2 <= len(path.parts) <= 64)
    with descriptors() as owned:
        rows = []
        for part in path.parts:
            tick()
            fd = owned.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                parent=rows[-1][0] if rows else None,
            )
            value = os.fstat(fd)
            require(stat.S_ISDIR(value.st_mode) and value.st_uid in {0, os.geteuid()})
            require(
                not stat.S_IMODE(value.st_mode) & 0o022
                or (value.st_uid == 0 and value.st_mode & stat.S_ISVTX)
            )
            acl.check(fd, ancestor=True)
            rows.append((fd, part, directory_identity(value)))
        private_directory(rows[-1][0], acl)
        yield rows[-1][0]
        for index, (fd, part, expected) in enumerate(rows):
            tick()
            acl.check(fd, ancestor=True)
            linked = os.stat(
                part,
                dir_fd=rows[index - 1][0] if index else None,
                follow_symlinks=False,
            )
            require(
                directory_identity(os.fstat(fd))
                == directory_identity(linked)
                == expected
            )


def file_map(
    values: tuple[tuple[str, bytes], ...], limits: dict[str, int]
) -> dict[str, bytes]:
    require(type(values) is tuple and len(values) == len(limits))
    result = {}
    for row in values:
        require(type(row) is tuple and len(row) == 2)
        name, raw = row
        require(type(name) is str and name in limits and name not in result)
        require(type(raw) is bytes and 0 < len(raw) <= limits[name])
        result[name] = raw
    require(
        set(result) == set(limits) and sum(map(len, result.values())) <= 12 * 1024**2
    )
    return result


def presence(at: datetime) -> None:
    now = plan.now_utc()
    plan.encoded_time(at)
    require(now - timedelta(minutes=5) <= at <= now)


def preparation_check(
    packet: PreparationPacket, expected: str, expected_review: str
) -> dict[str, bytes]:
    require(
        type(packet) is PreparationPacket
        and valid_sha(expected)
        and valid_sha(expected_review)
    )
    files = file_map(packet.files, PREPARATION)
    require(sha(files["preparation.json"]) == expected)
    p = record(files["preparation.json"])
    created, now = plan.timestamp(p["created_at"]), plan.now_utc()
    require(created <= now < plan.timestamp(p["expires_at"]))
    require(p["operator_uid"] == os.geteuid() and type(p["operator_uid"]) is int)
    source = plan.source_record(packet.source, p["source_provenance_sha256"], now)
    require(
        sha(packet.source_review.summary)
        == expected_review
        == p["source_review_summary_sha256"]
    )
    expected_files = plan.preparation_content(
        packet.source,
        packet.source_review,
        packet.workspace_manifest,
        expected_source_sha256=p["source_provenance_sha256"],
        expected_source_review_sha256=expected_review,
        expected_workspace_manifest_sha256=p["workspace_manifest_sha256"],
        hdiutil_sha256=p["hdiutil_sha256"],
        diskutil_sha256=p["diskutil_sha256"],
        operator_uid=p["operator_uid"],
        observation_root=p["observation_root"],
        human_presence_confirmed_at=plan.timestamp(p["human_presence_confirmed_at"]),
        created=created,
    )
    require(file_map(expected_files, PREPARATION) == files)
    require(source["source_commit"] == p["source_commit"])
    return files


def write_file(
    parent: int, name: str, raw: bytes, acl: ACL, tick: Callable[[], None]
) -> tuple[int, ...]:
    with descriptors() as owned:
        fd = owned.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            parent=parent,
        )
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid())
        require(
            stat.S_IMODE(before.st_mode) == 0o400
            and before.st_nlink == 1
            and before.st_size == 0
        )
        acl.check(fd)
        view = memoryview(raw)
        while view:
            tick()
            size = os.write(fd, view[:65536])
            require(size > 0)
            view = view[size:]
        os.fsync(fd)
        acl.check(fd)
        after = os.fstat(fd)
        require(directory_identity(before) == directory_identity(after))
        require(after.st_nlink == 1 and after.st_size == len(raw))
        require(
            file_identity(after)
            == file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
        )
        result = file_identity(after)
    return result


def read_file(
    parent: int,
    name: str,
    raw: bytes,
    expected: tuple[int, ...],
    acl: ACL,
    tick: Callable[[], None],
) -> None:
    with descriptors() as owned:
        fd = owned.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            parent=parent,
        )
        before = os.fstat(fd)
        # A caller-supplied Publication is not permission evidence. Enforce the
        # file policy independently even if its supplied stat tuple matches.
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid())
        require(stat.S_IMODE(before.st_mode) == 0o400 and before.st_nlink == 1)
        require(before.st_size == len(raw) and file_identity(before) == expected)
        acl.check(fd)
        chunks, remaining = [], len(raw)
        while remaining:
            tick()
            block = os.read(fd, min(65536, remaining))
            require(block)
            chunks.append(block)
            remaining -= len(block)
        require(not os.read(fd, 1) and b"".join(chunks) == raw)
        acl.check(fd)
        require(
            file_identity(os.fstat(fd))
            == file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            == expected
        )


def publish(
    check: Callable[[], dict[str, bytes]],
    kind: str,
    token: str,
    confirmed_at: datetime,
    prior: Publication | None = None,
) -> Publication:
    """Internal closed callers only; prior selects READ-ONLY revalidation."""
    require(kind in {"preparation", "snapshot"})
    require(type(token) is str and re.fullmatch(r"[0-9a-f]{32}", token))
    name = "human-" + kind + "-" + token
    path = PACKET_PARENT / name
    with operation() as tick:
        presence(confirmed_at)
        files = check()
        anchor = "preparation.json" if kind == "preparation" else "handoff.json"
        observation = (
            Path(record(files[anchor])["observation_root"])
            if kind == "preparation"
            else None
        )
        if observation is not None:
            require(
                observation.parent == PACKET_PARENT
                and re.fullmatch(r"human-observation-[0-9a-f]{32}", observation.name)
            )
        if prior is not None:
            require(
                type(prior) is Publication
                and prior.packet == path
                and prior.observation_directory == observation
            )
        acl = ACL()
        with protected_parent(acl, tick) as parent, descriptors() as owned:
            if prior is None:
                # No exist_ok, replacement, rename, retry or recursive cleanup.
                os.mkdir(name, mode=0o700, dir_fd=parent)
            root = owned.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                parent=parent,
            )
            directory = private_directory(root, acl)
            require(prior is None or directory == prior.directory_identity)
            require(prior is not None or not os.listdir(root))
            observation_fd, observation_identity = None, None
            if observation is not None:
                if prior is None:
                    os.mkdir(observation.name, mode=0o700, dir_fd=parent)
                observation_fd = owned.open(
                    observation.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    parent=parent,
                )
                observation_identity = private_directory(observation_fd, acl)
                require(not os.listdir(observation_fd))
                require(
                    prior is None
                    or observation_identity == prior.observation_directory_identity
                )
            identities = dict(prior.file_identities) if prior is not None else {}
            if prior is not None:
                require(
                    prior.file_sha256
                    == tuple(sorted((key, sha(raw)) for key, raw in files.items()))
                )
                require(
                    len(prior.file_identities) == len(files)
                    and set(identities) == set(files)
                )
            else:
                for key in [*sorted(set(files) - {anchor}), anchor]:
                    tick()
                    if key == anchor:
                        presence(confirmed_at)
                        require(check() == files)
                    identities[key] = write_file(root, key, files[key], acl, tick)
                os.fsync(root)
                if observation_fd is not None:
                    os.fsync(observation_fd)
                os.fsync(parent)
            require(set(os.listdir(root)) == set(files))
            for key, raw in files.items():
                read_file(root, key, raw, identities[key], acl, tick)
            require(private_directory(root, acl) == directory)
            require(
                directory_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                == directory
            )
            if observation_fd is not None and observation is not None:
                require(private_directory(observation_fd, acl) == observation_identity)
                require(
                    directory_identity(
                        os.stat(observation.name, dir_fd=parent, follow_symlinks=False)
                    )
                    == observation_identity
                )
                require(not os.listdir(observation_fd))
            presence(confirmed_at)
            require(check() == files)
            result = Publication(
                path,
                directory,
                tuple(sorted((key, sha(raw)) for key, raw in files.items())),
                tuple(sorted(identities.items())),
                observation,
                observation_identity,
            )
        # Descriptor close, ancestor-link and timer failures cannot return success.
        tick()
        presence(confirmed_at)
        require(check() == files)
    return result


def publish_preparation(
    packet: PreparationPacket,
    *,
    expected_preparation_sha256: str,
    expected_source_review_sha256: str,
    packet_token: str,
    human_presence_confirmed_at: datetime,
    revalidate: Publication | None = None,
) -> Publication:
    """Publish or revalidate six public files; exact human-command review still owed."""
    try:
        return publish(
            lambda: preparation_check(
                packet, expected_preparation_sha256, expected_source_review_sha256
            ),
            "preparation",
            packet_token,
            human_presence_confirmed_at,
            revalidate,
        )
    except BaseException:
        raise PacketPublicationRefused("snapshot_packet_publication_refused") from None


def publish_capture(
    draft: plan.HandoffDraft,
    handoff_review: ReviewRecord,
    *,
    expected_handoff_sha256: str,
    expected_handoff_review_sha256: str,
    packet_token: str,
    human_presence_confirmed_at: datetime,
    revalidate: Publication | None = None,
) -> Publication:
    """Only already reviewed exact handoff files; no plan/receipt generation or renewal."""

    def check() -> dict[str, bytes]:
        result = file_map(
            plan.packet_files(
                draft,
                handoff_review,
                expected_handoff_sha256=expected_handoff_sha256,
                expected_handoff_review_sha256=expected_handoff_review_sha256,
            ),
            CAPTURE,
        )
        require(record(result["handoff.json"])["operator_uid"] == os.geteuid())
        return result

    try:
        return publish(
            check, "snapshot", packet_token, human_presence_confirmed_at, revalidate
        )
    except BaseException:
        raise PacketPublicationRefused("snapshot_packet_publication_refused") from None


if __name__ == "__main__":
    raise SystemExit(
        "snapshot_packet_publication_requires_authenticated_reviewed_inputs"
    )
