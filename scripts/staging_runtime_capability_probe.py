"""Synthetic-only Linux prerequisite probe, NOT a verifier or confinement proof.

Execute only the exact reviewed image in an explicitly authorized disposable
context. This mutates namespaces of ONE fixed child, never the parent or host.
There are no commands, credentials, files, URLs or policies supplied by a caller.
No import-time OS activity. See the flight plan for deliberately untested gates.
"""

from __future__ import annotations

import ctypes
import errno
import importlib.util
import json
import os
import platform
import select
import signal
import sys
import time
from pathlib import Path
from typing import Any

UID = 65532
MAX_REPORT_BYTES = 4096
WALL_SECONDS = 25
CLEANUP_SECONDS = 2
CLONE_NEWUSER = 0x10000000
CLONE_NEWNET = 0x40000000
CLONE_NEWNS = 0x00020000
CLONE_NEWPID = 0x20000000
PHASES = (
    "context",
    "no_new_privileges",
    "user_namespace",
    "identity_map",
    "network_namespace",
    "network_interfaces",
    "mount_namespace",
    "pid_namespace_request",
    "capability_drop",
    "final_status",
)
UNPROVEN = (
    "azure_platform_identity_and_exact_execution_binding",
    "read_only_root_and_all_writable_mounts",
    "actual_pid_namespace_child_and_cgroup_limits",
    "hostile_descriptor_and_descendant_containment",
    "atomic_persistent_isolation_for_a_real_verifier",
    "separate_publisher_and_executor_evidence",
    "independent_exact_containment_review",
)
REASONS = frozenset(
    {"denied", "unavailable", "resource_limit", "verification_failed", "internal"}
)
KERNEL_PHASES = frozenset({1, 2, 4, 6, 7, 8})


class ProbeRefused(RuntimeError):
    """A fixed-code failure, without raw system metadata or exception text."""


class KernelAdmissionRefused(RuntimeError):
    """Only an explicit prctl/unshare/capset return can produce this signal."""

    def __init__(self, reason: str) -> None:
        if reason not in {"denied", "unavailable"}:
            raise ProbeRefused()
        self.reason = reason


class _Header(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _Caps(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


class _Linux:
    def __init__(self) -> None:
        self.lib = ctypes.CDLL(None, use_errno=True)
        self.lib.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        self.lib.prctl.restype = ctypes.c_int
        self.lib.unshare.argtypes = [ctypes.c_int]
        self.lib.unshare.restype = ctypes.c_int
        self.lib.syscall.argtypes = [
            ctypes.c_long,
            ctypes.POINTER(_Header),
            ctypes.POINTER(_Caps),
        ]
        self.lib.syscall.restype = ctypes.c_long

    @staticmethod
    def checked(result: int) -> None:
        if result != 0:
            number = ctypes.get_errno()
            if number in (errno.EACCES, errno.EPERM):
                raise KernelAdmissionRefused("denied")
            if number in (errno.ENOSYS, errno.EINVAL):
                raise KernelAdmissionRefused("unavailable")
            # OSError carries an errno only; its text is never serialized.
            raise OSError(number, "kernel_operation_failed")

    def prctl(self, option: int, argument: int = 0) -> None:
        self.checked(self.lib.prctl(option, argument, 0, 0, 0))

    def unshare(self, flag: int) -> None:
        self.checked(self.lib.unshare(flag))

    def drop_capabilities(self) -> None:
        with open("/proc/sys/kernel/cap_last_cap", "rb") as stream:
            raw = stream.read(17)
        if not raw.endswith(b"\n") or not raw[:-1].isdigit() or len(raw) > 3:
            raise ProbeRefused()
        last = int(raw)
        if not 0 <= last <= 63:
            raise ProbeRefused()
        for capability in range(last + 1):
            self.prctl(24, capability)  # PR_CAPBSET_DROP, in the NEW user namespace
        self.prctl(47, 4)  # PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL
        header = _Header(0x20080522, 0)  # Linux capability ABI v3, self only
        values = (_Caps * 2)()
        # capset has no portable libc wrapper; 126 is __NR_capset on the ONLY
        # supported architecture, Linux x86_64, checked before constructing us.
        self.checked(self.lib.syscall(126, ctypes.byref(header), values))


def _identity() -> None:
    if (
        sys.platform != "linux"
        or platform.machine() != "x86_64"
        or os.getresuid() != (UID, UID, UID)
        or os.getresgid() != (UID, UID, UID)
        or os.getgroups() not in ([], [UID])
    ):
        raise ProbeRefused()


def _namespace() -> tuple[tuple[int, int], ...]:
    result = []
    for name in ("user", "net", "mnt"):
        value = os.stat(f"/proc/thread-self/ns/{name}")
        result.append((value.st_dev, value.st_ino))
    return tuple(result)


def _map_identity() -> None:
    # Map the same single non-root UID/GID; never map or become UID 0. Denying
    # setgroups is mandatory before an unprivileged gid_map write, with no fallback.
    for name, value in (
        ("uid_map", f"{UID} {UID} 1\n".encode("ascii")),
        ("setgroups", b"deny\n"),
        ("gid_map", f"{UID} {UID} 1\n".encode("ascii")),
    ):
        descriptor = os.open(f"/proc/self/{name}", os.O_WRONLY | os.O_CLOEXEC)
        try:
            if os.write(descriptor, value) != len(value):
                raise ProbeRefused()
        finally:
            os.close(descriptor)
    _identity()


def _diagnostic_module() -> Any:
    # -I -S deliberately excludes the workdir, environment and site-packages.
    # Load only the reviewed sibling copied into the immutable image.
    path = Path(__file__).with_name("staging_offline_verifier_diagnostics.py")
    spec = importlib.util.spec_from_file_location("probe_diagnostics", path)
    if spec is None or spec.loader is None:
        raise ProbeRefused()
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reason(error: Exception) -> str:
    if isinstance(error, ProbeRefused):
        return "verification_failed"
    if isinstance(error, OSError):
        if error.errno in (errno.EACCES, errno.EPERM):
            return "denied"
        if error.errno in (errno.ENOSYS, errno.EINVAL, errno.ENOENT):
            return "unavailable"
        if error.errno in (errno.ENOMEM, errno.EAGAIN, errno.ENOSPC, errno.EMFILE):
            return "resource_limit"
    return "internal"


def _result(
    completed: int, reason: str | None, *, kernel_refused: bool = False
) -> dict[str, Any]:
    if kernel_refused and (
        completed not in KERNEL_PHASES or reason not in {"denied", "unavailable"}
    ):
        raise ProbeRefused()
    outcome = "prerequisites_observed"
    if reason is not None:
        outcome = "unsupported_or_unavailable" if kernel_refused else "probe_error"
    return {
        "schema_version": 1,
        "kind": "runtime-namespace-prerequisites-v1",
        "diagnostic_only": True,
        "runtime_acceptance": False,
        "activation_authorized": False,
        "outcome": outcome,
        "completed_phases": list(PHASES[:completed]),
        "failed_phase": None if reason is None else PHASES[completed],
        "reason": reason,
        "unproven_requirements": list(UNPROVEN),
    }


def _probe(kernel: _Linux) -> dict[str, Any]:
    completed = 0
    try:
        _identity()
        diagnostic = _diagnostic_module()
        before = _namespace()  # This very child, BEFORE any namespace mutation.
        completed += 1
        kernel.prctl(38, 1)  # PR_SET_NO_NEW_PRIVS, irreversible in this child
        completed += 1
        kernel.unshare(CLONE_NEWUSER)
        if _namespace()[0] == before[0]:
            raise ProbeRefused()
        completed += 1
        _map_identity()
        completed += 1
        kernel.unshare(CLONE_NEWNET)
        if _namespace()[1] == before[1]:
            raise ProbeRefused()
        completed += 1
        network = diagnostic._interfaces(diagnostic._KernelView().interfaces())
        if any(
            network.get(name) is not True
            for name in (
                "only_loopback_interface_observed",
                "loopback_down_observed",
            )
        ):
            raise ProbeRefused()
        completed += 1
        kernel.unshare(CLONE_NEWNS)
        if _namespace()[2] == before[2]:
            raise ProbeRefused()
        completed += 1
        # NEWPID affects FUTURE children. No grandchild is spawned here; this
        # records only successful admission of the syscall, not PID confinement.
        kernel.unshare(CLONE_NEWPID)
        completed += 1
        kernel.drop_capabilities()
        completed += 1
        status = diagnostic._status(
            diagnostic._KernelView().read(diagnostic.STATUS_PATH)
        )
        if any(
            status.get(name) is not True
            for name in (
                "all_real_effective_saved_filesystem_uids_nonzero",
                "all_capability_sets_zero",
                "no_new_privileges",
            )
        ):
            raise ProbeRefused()
        completed += 1
    except KernelAdmissionRefused as error:
        return _result(completed, error.reason, kernel_refused=True)
    except Exception as error:
        # Import/parser/context/identity-map/verification failures are NOT
        # evidence of syscall admission failure, even when errno is EPERM.
        return _result(completed, _reason(error))
    return _result(completed, None)


def _child(write_fd: int, expected_parent: int) -> None:
    # No exec, fork, namespace entry, mount, network send or arbitrary callback
    # after this fixed worker starts. Its only write is its own uid/gid mapping
    # plus one bounded JSON result to the anonymous parent pipe.
    exit_code = 3
    try:
        import resource

        for descriptor in (0, 1, 2):
            os.close(descriptor)
        os.environ.clear()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (134217728, 134217728))
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        kernel = _Linux()
        kernel.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG; check the setup race.
        if os.getppid() != expected_parent:
            raise ProbeRefused()
        report = _probe(kernel)
        raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        if len(raw) >= MAX_REPORT_BYTES or os.write(write_fd, raw) != len(raw):
            raise ProbeRefused()
        exit_code = 0
    except BaseException:
        # No exception text, traceback, metadata, token or raw proc contents.
        pass
    os._exit(exit_code)


def _validate(raw: bytes) -> dict[str, Any]:
    if not 0 < len(raw) < MAX_REPORT_BYTES:
        raise ProbeRefused()
    value = json.loads(raw)
    # Compare against the finite set of whole canonical outcomes, rather than
    # trusting arbitrary child-supplied booleans or accepting additional fields.
    allowed = (
        [_result(len(PHASES), None)]
        + [_result(index, reason) for index in range(len(PHASES)) for reason in REASONS]
        + [
            _result(index, reason, kernel_refused=True)
            for index in KERNEL_PHASES
            for reason in ("denied", "unavailable")
        ]
    )
    if not any(
        json.dumps(value, sort_keys=True) == json.dumps(candidate, sort_keys=True)
        for candidate in allowed
    ):
        raise ProbeRefused()
    return value


def _receive(read_fd: int, deadline: float) -> bytes:
    raw = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeRefused()
        ready, _, _ = select.select([read_fd], [], [], remaining)
        if not ready:
            raise ProbeRefused()
        chunk = os.read(read_fd, MAX_REPORT_BYTES - len(raw))
        if not chunk:
            return bytes(raw)
        raw.extend(chunk)
        if len(raw) >= MAX_REPORT_BYTES:
            raise ProbeRefused()


def _close_inherited() -> None:
    # A hard rlimit is NOT the largest existing descriptor (it can be lowered
    # after opening an FD). Enumerate a bounded set instead, before creating the
    # pipe. This exact -I -S single-threaded entrypoint has no concurrent openers.
    for descriptor in (0, 1, 2):
        os.fstat(descriptor)
    descriptors = []
    with os.scandir("/proc/self/fd") as entries:
        for entry in entries:
            if (
                len(descriptors) >= 256
                or not entry.name.isascii()
                or not entry.name.isdigit()
            ):
                raise ProbeRefused()
            number = int(entry.name)
            if not 0 <= number <= 2_147_483_647:
                raise ProbeRefused()
            descriptors.append(number)
    for descriptor in descriptors:
        if descriptor >= 3:
            try:
                os.close(descriptor)
            except OSError as error:
                # scandir's own descriptor is already closed by its context.
                if error.errno != errno.EBADF:
                    raise


def _reap_after_kill(child: int) -> bool:
    deadline = time.monotonic() + CLEANUP_SECONDS
    while True:
        try:
            waited, _ = os.waitpid(child, os.WNOHANG)
        except ChildProcessError:
            return False
        if waited == child:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        select.select([], [], [], min(0.01, remaining))


def _run() -> dict[str, Any]:
    _identity()
    _close_inherited()
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    child = -1
    try:
        deadline = time.monotonic() + WALL_SECONDS
        parent = os.getpid()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            _child(write_fd, parent)
            os._exit(3)
        os.close(write_fd)
        write_fd = -1
        report = _validate(_receive(read_fd, deadline))
        # Reap without an unbounded wait. A valid report alone is insufficient.
        while time.monotonic() < deadline:
            waited, status = os.waitpid(child, os.WNOHANG)
            if waited == child:
                child = -1
                if status != 0:
                    raise ProbeRefused()
                return report
            select.select([], [], [], min(0.01, max(0, deadline - time.monotonic())))
        raise ProbeRefused()
    finally:
        try:
            if child > 0:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # Allow ordinary killable children to become waitable. D-state
                # tasks still require the external disposable-job backstop.
                if not _reap_after_kill(child):
                    raise ProbeRefused()
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise ProbeRefused()
        report = _run()
        raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        os.set_blocking(1, False)
        if os.write(1, raw) != len(raw):
            return 3
        return {
            "prerequisites_observed": 0,
            "unsupported_or_unavailable": 2,
            "probe_error": 3,
        }[report["outcome"]]
    except BaseException:
        # Execution-context/transport failures are not namespace-denial evidence.
        return 3


if __name__ == "__main__":
    sys.exit(main())
