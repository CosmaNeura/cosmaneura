"""Dormant, read-only Linux observations; never containment or activation proof.

No executable path imports this module. A separately authorized runtime capture
may call ``capture_linux_verifier_diagnostics`` after exact-source review. This
does not run gh, create namespaces, change capabilities/mounts, or send network
traffic. Interface flags use a local kernel ioctl, not sysfs, whose network view
can belong to the namespace in which sysfs was mounted.
"""

from __future__ import annotations

import fcntl
import re
import socket
import struct
import sys
from typing import Any, Protocol

MAXIMUM_FILE_BYTES = 65_536
MAXIMUM_INTERFACES = 64
IFF_UP = 0x1
IFF_LOOPBACK = 0x8
STATUS_PATH = "/proc/thread-self/status"
MOUNTS_PATH = "/proc/thread-self/mountinfo"
CGROUP_PATH = "/proc/thread-self/cgroup"
NETNS_PATH = "/proc/thread-self/ns/net"
PID1_NETNS_PATH = "/proc/1/ns/net"
READ_PATHS = frozenset({STATUS_PATH, MOUNTS_PATH, CGROUP_PATH})
LINK_PATHS = frozenset({NETNS_PATH, PID1_NETNS_PATH})
CAPABILITY_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
INTERFACE_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,15}")
UNPROVEN = (
    "authorized_exact_image_and_platform_capture",
    "trusted_outer_network_namespace_binding",
    "atomic_and_persistent_isolation",
    "inherited_descriptor_and_process_tree_containment",
    "complete_filesystem_and_pid_cgroup_confinement",
    "independent_exact_containment_review",
)


class DiagnosticsUnavailable(RuntimeError):
    """Bounded diagnostic failure; never carries file contents or OS errors."""


class _View(Protocol):
    def read(self, path: str) -> bytes: ...

    def readlink(self, path: str) -> str: ...

    def interfaces(self) -> list[tuple[str, int]]: ...


def _text(raw: bytes) -> str:
    if type(raw) is not bytes or not 0 < len(raw) <= MAXIMUM_FILE_BYTES:
        raise DiagnosticsUnavailable("invalid_observation")
    try:
        return raw.decode("ascii", errors="strict")
    except UnicodeError:
        raise DiagnosticsUnavailable("invalid_observation") from None


class _KernelView:
    def read(self, path: str) -> bytes:
        if path not in READ_PATHS:
            raise DiagnosticsUnavailable("unsupported_path")
        with open(path, "rb") as stream:
            raw = stream.read(MAXIMUM_FILE_BYTES + 1)
        _text(raw)
        return raw

    def readlink(self, path: str) -> str:
        if path not in LINK_PATHS:
            raise DiagnosticsUnavailable("unsupported_path")
        # Importing this library never opens procfs or creates a socket.
        import os

        return os.readlink(path)

    def interfaces(self) -> list[tuple[str, int]]:
        names = socket.if_nameindex()
        if len(names) > MAXIMUM_INTERFACES:
            raise DiagnosticsUnavailable("invalid_observation")
        result = []
        # SIOCGIFFLAGS reads this thread's current network namespace. No bind,
        # connect, send, listen, DNS lookup or remote endpoint is involved.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            for _index, name in names:
                if type(name) is not str or INTERFACE_NAME.fullmatch(name) is None:
                    raise DiagnosticsUnavailable("invalid_observation")
                request = struct.pack("256s", name.encode("ascii"))
                response = fcntl.ioctl(control.fileno(), 0x8913, request)
                flags = struct.unpack_from("@H", response, 16)[0]
                result.append((name, flags))
        return result


def _status(raw: bytes) -> dict[str, bool | int | None]:
    wanted = {"Uid", "NoNewPrivs", "Seccomp", *CAPABILITY_FIELDS}
    values: dict[str, str] = {}
    for line in _text(raw).splitlines():
        name, separator, value = line.partition(":")
        if separator and name in wanted:
            if name in values:
                raise DiagnosticsUnavailable("invalid_observation")
            values[name] = value.strip()
    if not wanted - {"Seccomp"} <= values.keys():
        raise DiagnosticsUnavailable("invalid_observation")
    uids = values["Uid"].split()
    if len(uids) != 4 or any(
        re.fullmatch(r"0|[1-9][0-9]{0,9}", value) is None or int(value) > 4_294_967_295
        for value in uids
    ):
        raise DiagnosticsUnavailable("invalid_observation")
    caps = [values[name] for name in CAPABILITY_FIELDS]
    if any(re.fullmatch(r"[0-9a-fA-F]{16}", value) is None for value in caps):
        raise DiagnosticsUnavailable("invalid_observation")
    if values["NoNewPrivs"] not in {"0", "1"}:
        raise DiagnosticsUnavailable("invalid_observation")
    seccomp = values.get("Seccomp")
    if seccomp is not None and seccomp not in {"0", "1", "2"}:
        raise DiagnosticsUnavailable("invalid_observation")
    return {
        "all_real_effective_saved_filesystem_uids_nonzero": all(int(v) for v in uids),
        "all_capability_sets_zero": all(int(v, 16) == 0 for v in caps),
        "no_new_privileges": values["NoNewPrivs"] == "1",
        "seccomp_mode_observed": None if seccomp is None else int(seccomp),
    }


def _root_mount(raw: bytes) -> bool:
    roots = []
    for line in _text(raw).splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields[6:]:
            raise DiagnosticsUnavailable("invalid_observation")
        separator = fields.index("-", 6)
        if len(fields) != separator + 4:
            raise DiagnosticsUnavailable("invalid_observation")
        if fields[4] == "/":
            access = set(fields[5].split(",")) & {"ro", "rw"}
            if len(access) != 1:
                raise DiagnosticsUnavailable("invalid_observation")
            roots.append(access == {"ro"})
    if len(roots) != 1:
        raise DiagnosticsUnavailable("invalid_observation")
    return roots[0]


def _cgroup(raw: bytes) -> bool:
    rows = _text(raw).splitlines()
    if not rows or any(
        re.fullmatch(r"[0-9]+:[A-Za-z0-9_=,.-]*:/[^\x00-\x1f]*", row) is None
        for row in rows
    ):
        raise DiagnosticsUnavailable("invalid_observation")
    unified = [row for row in rows if row.startswith("0::")]
    if len(unified) > 1:
        raise DiagnosticsUnavailable("invalid_observation")
    # Membership is not a pids.max, namespace ownership or process-kill proof.
    return len(unified) == 1


def _network_namespace_differs(view: _View) -> bool:
    current = view.readlink(NETNS_PATH)
    pid1 = view.readlink(PID1_NETNS_PATH)
    if any(
        type(value) is not str or re.fullmatch(r"net:\[[0-9]{1,20}\]", value) is None
        for value in (current, pid1)
    ):
        raise DiagnosticsUnavailable("invalid_observation")
    # PID 1 may itself be inside the verifier's PID namespace. It is only a
    # comparison point, NOT an authenticated outer network namespace.
    return current != pid1


def _interfaces(values: list[tuple[str, int]]) -> dict[str, bool | None]:
    if type(values) is not list or len(values) > MAXIMUM_INTERFACES:
        raise DiagnosticsUnavailable("invalid_observation")
    observed: dict[str, int] = {}
    for item in values:
        if type(item) is not tuple or len(item) != 2:
            raise DiagnosticsUnavailable("invalid_observation")
        name, flags = item
        if (
            type(name) is not str
            or INTERFACE_NAME.fullmatch(name) is None
            or name in observed
            or type(flags) is not int
            or not 0 <= flags <= 0xFFFF
        ):
            raise DiagnosticsUnavailable("invalid_observation")
        observed[name] = flags
    # Names are not interface identity: lo can be misleading, and a real
    # loopback can be renamed. More than one flagged loopback is ambiguous.
    loopbacks = [flags for flags in observed.values() if flags & IFF_LOOPBACK]
    return {
        "only_loopback_interface_observed": len(observed) == len(loopbacks) == 1,
        "loopback_down_observed": None
        if len(loopbacks) != 1
        else not loopbacks[0] & IFF_UP,
    }


def _collect(view: _View) -> dict[str, Any]:
    observations: dict[str, bool | int | None] = {
        "all_real_effective_saved_filesystem_uids_nonzero": None,
        "all_capability_sets_zero": None,
        "no_new_privileges": None,
        "seccomp_mode_observed": None,
        "root_mount_read_only_observed": None,
        "unified_cgroup_membership_observed": None,
        "network_namespace_differs_from_pid1": None,
        "only_loopback_interface_observed": None,
        "loopback_down_observed": None,
    }
    unavailable = []
    readers = (
        ("status", lambda: _status(view.read(STATUS_PATH))),
        (
            "mounts",
            lambda: {
                "root_mount_read_only_observed": _root_mount(view.read(MOUNTS_PATH))
            },
        ),
        (
            "cgroup",
            lambda: {
                "unified_cgroup_membership_observed": _cgroup(view.read(CGROUP_PATH))
            },
        ),
        (
            "namespace_comparison",
            lambda: {
                "network_namespace_differs_from_pid1": _network_namespace_differs(view)
            },
        ),
        ("interfaces", lambda: _interfaces(view.interfaces())),
    )
    for label, read in readers:
        try:
            observations.update(read())
        except (DiagnosticsUnavailable, OSError, ValueError, struct.error):
            # Do not expose raw file data, cgroup paths, UID values or OS errors.
            unavailable.append(label)
    return {
        "schema_version": 1,
        "kind": "offline-verifier-diagnostics-v1",
        "diagnostic_only": True,
        "runtime_acceptance": False,
        "activation_authorized": False,
        "observations": observations,
        "unavailable_observations": unavailable,
        "unproven_requirements": list(UNPROVEN),
    }


def capture_linux_verifier_diagnostics() -> dict[str, Any]:
    """Read bounded kernel metadata on Linux; never execute a verifier or job.

    Requires a separately authorized capture context. Facts are non-atomic and
    unauthenticated; even entirely favorable observations cannot grant acceptance.
    There is deliberately no CLI, policy input, gate setter or consumer wiring.
    """
    if sys.platform != "linux":
        raise DiagnosticsUnavailable("platform_unsupported")
    return _collect(_KernelView())
