"""Dormant, read-only Linux observations; never containment or activation proof.

The fixed synthetic ``staging_runtime_capability_probe.py`` is an executable
consumer of this module's bounded readers/parsers. Import itself performs no
capture or mutation. A separately authorized runtime capture may also call
``capture_linux_verifier_diagnostics`` after exact-source review. These readers
do not run gh, create namespaces, change capabilities/mounts, or send network
traffic. Interface flags use a local kernel ioctl, not sysfs, whose network view
can belong to the namespace in which sysfs was mounted. No production consumer
or policy gate imports or accepts this diagnostic as containment proof.
"""

from __future__ import annotations

import re
import socket
import struct
import sys
from typing import Any, Protocol

MAXIMUM_FILE_BYTES = 65_536
MAXIMUM_INTERFACES = 64
# Linux ns_common.inum is unsigned int; accept only the positive uint32 subset.
MAXIMUM_NAMESPACE_INODE = 4_294_967_295
IFF_UP = 0x1
IFF_LOOPBACK = 0x8
STATUS_PATH = "/proc/thread-self/status"
MOUNTS_PATH = "/proc/thread-self/mountinfo"
CGROUP_PATH = "/proc/thread-self/cgroup"
NETDEV_PATH = "/proc/thread-self/net/dev"
NETNS_PATH = "/proc/thread-self/ns/net"
PID1_NETNS_PATH = "/proc/1/ns/net"
READ_PATHS = frozenset({STATUS_PATH, MOUNTS_PATH, CGROUP_PATH, NETDEV_PATH})
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


def _rows(raw: bytes, *, allow_tabs: bool = False) -> list[str]:
    text = _text(raw)
    controls = (
        r"[\x00-\x08\x0b-\x1f\x7f]" if allow_tabs else r"[\x00-\x09\x0b-\x1f\x7f]"
    )
    if not text.endswith("\n") or re.search(controls, text):
        raise DiagnosticsUnavailable("invalid_observation")
    rows = text[:-1].split("\n")
    if any(not row.strip(" \t") for row in rows):
        raise DiagnosticsUnavailable("invalid_observation")
    return rows


def _decimal(value: str, *, positive: bool = False) -> bool:
    return (
        re.fullmatch(r"0|[1-9][0-9]{0,19}", value) is not None
        and int(value) <= 18_446_744_073_709_551_615
        and (not positive or value != "0")
    )


def _encoded_token(value: str, *, absolute: bool = False) -> bool:
    # mountinfo escapes only space, tab, newline and backslash with these octals.
    return (
        bool(value)
        and (not absolute or value.startswith("/"))
        and re.fullmatch(r"(?:[^\x00-\x20\x7f\\]|\\(?:040|011|012|134))+", value)
        is not None
    )


def _mount_options(value: str) -> set[str]:
    options = value.split(",")
    keys = set()
    for option in options:
        key, separator, argument = option.partition("=")
        if (
            re.fullmatch(r"[A-Za-z0-9_.-]+", key) is None
            or key in keys
            or (key in {"ro", "rw"} and separator)
            or (separator and not _encoded_token(argument))
        ):
            raise DiagnosticsUnavailable("invalid_observation")
        keys.add(key)
    if len(keys & {"ro", "rw"}) != 1:
        raise DiagnosticsUnavailable("invalid_observation")
    return set(options)


def _interface_names(raw: bytes) -> list[str]:
    rows = _rows(raw)
    if not 2 <= len(rows) <= MAXIMUM_INTERFACES + 2:
        raise DiagnosticsUnavailable("invalid_observation")
    headings = [part.strip() for part in rows[0].split("|")]
    counters = [part.split() for part in rows[1].split("|")]
    if headings != ["Inter-", "Receive", "Transmit"] or counters != [
        ["face"],
        "bytes packets errs drop fifo frame compressed multicast".split(),
        "bytes packets errs drop fifo colls carrier compressed".split(),
    ]:
        raise DiagnosticsUnavailable("invalid_observation")
    names = []
    for row in rows[2:]:
        padded, separator, data = row.rpartition(":")
        name = padded.strip()
        values = data.split()
        if (
            not separator
            or INTERFACE_NAME.fullmatch(name) is None
            or name in names
            or len(values) != 16
            or not all(_decimal(value) for value in values)
        ):
            raise DiagnosticsUnavailable("invalid_observation")
        names.append(name)
    return names


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
        # Bound intake before allocating/parsing interface lists; no if_nameindex
        # or sysfs fallback. This proc path reflects the observing thread's netns.
        names = _interface_names(self.read(NETDEV_PATH))
        try:
            import fcntl
        except ImportError:
            raise DiagnosticsUnavailable("kernel_interface_unavailable") from None
        result = []
        # SIOCGIFFLAGS reads this thread's current network namespace. No bind,
        # connect, send, listen, DNS lookup or remote endpoint is involved.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            for name in names:
                request = struct.pack("256s", name.encode("ascii"))
                response = fcntl.ioctl(control.fileno(), 0x8913, request)
                flags = struct.unpack_from("@H", response, 16)[0]
                result.append((name, flags))
        return result


def _status(raw: bytes) -> dict[str, bool | int | None]:
    wanted = {"Uid", "NoNewPrivs", "Seccomp", *CAPABILITY_FIELDS}
    values: dict[str, str] = {}
    seen = set()
    for line in _rows(raw, allow_tabs=True):
        name, separator, value = line.partition(":")
        if (
            not separator
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) is None
            or name in seen
        ):
            raise DiagnosticsUnavailable("invalid_observation")
        seen.add(name)
        if name in wanted:
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
    mount_ids = set()
    for line in _rows(raw):
        fields = line.split(" ")
        if len(fields) < 10 or "-" not in fields[6:]:
            raise DiagnosticsUnavailable("invalid_observation")
        separator = fields.index("-", 6)
        if len(fields) != separator + 4:
            raise DiagnosticsUnavailable("invalid_observation")
        device = fields[2].split(":")
        if (
            not _decimal(fields[0], positive=True)
            or not _decimal(fields[1], positive=True)
            or fields[0] in mount_ids
            or len(device) != 2
            or not all(_decimal(part) and int(part) <= 4_294_967_295 for part in device)
            or not _encoded_token(fields[3], absolute=True)
            or not _encoded_token(fields[4], absolute=True)
            or re.fullmatch(r"[A-Za-z0-9_.-]+", fields[separator + 1]) is None
            or not _encoded_token(fields[separator + 2])
        ):
            raise DiagnosticsUnavailable("invalid_observation")
        mount_ids.add(fields[0])
        options = _mount_options(fields[5])
        _mount_options(fields[separator + 3])
        optional = set()
        for field in fields[6:separator]:
            tag, colon, value = field.partition(":")
            if tag in optional or (
                field != "unbindable"
                and (
                    tag not in {"shared", "master", "propagate_from"}
                    or not colon
                    or not _decimal(value, positive=True)
                )
            ):
                raise DiagnosticsUnavailable("invalid_observation")
            optional.add(tag)
        if ("unbindable" in optional and len(optional) != 1) or (
            "propagate_from" in optional and "master" not in optional
        ):
            raise DiagnosticsUnavailable("invalid_observation")
        if fields[4] == "/":
            roots.append("ro" in options)
    if len(roots) != 1:
        raise DiagnosticsUnavailable("invalid_observation")
    return roots[0]


def _cgroup(raw: bytes) -> bool:
    hierarchies = set()
    controllers = set()
    for row in _rows(raw):
        fields = row.split(":", 2)
        if len(fields) != 3:
            raise DiagnosticsUnavailable("invalid_observation")
        hierarchy, controller_field, path = fields
        if (
            not _decimal(hierarchy)
            or int(hierarchy) > 4_294_967_295
            or hierarchy in hierarchies
            or not path.startswith("/")
            or (hierarchy == "0") != (controller_field == "")
        ):
            raise DiagnosticsUnavailable("invalid_observation")
        hierarchies.add(hierarchy)
        selected = controller_field.split(",") if controller_field else []
        named = [
            controller for controller in selected if controller.startswith("name=")
        ]
        # proc_cgroup_show emits subsystem names, then an optional hierarchy name.
        # Named hierarchies can have controllers; name= must occur once, last.
        if len(named) > 1 or (named and selected[-1] != named[0]):
            raise DiagnosticsUnavailable("invalid_observation")
        for controller in selected:
            if (
                re.fullmatch(
                    r"(?:[A-Za-z_][A-Za-z0-9_.-]*|name=[A-Za-z0-9_.-]+)", controller
                )
                is None
                or controller in controllers
            ):
                raise DiagnosticsUnavailable("invalid_observation")
            controllers.add(controller)
    # Membership is not a pids.max, namespace ownership or process-kill proof.
    return "0" in hierarchies


def _network_namespace_differs(view: _View) -> bool:
    current = view.readlink(NETNS_PATH)
    pid1 = view.readlink(PID1_NETNS_PATH)
    for value in (current, pid1):
        if type(value) is not str:
            raise DiagnosticsUnavailable("invalid_observation")
        token = re.fullmatch(r"net:\[([1-9][0-9]{0,9})\]", value)
        if token is None or int(token[1]) > MAXIMUM_NAMESPACE_INODE:
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
