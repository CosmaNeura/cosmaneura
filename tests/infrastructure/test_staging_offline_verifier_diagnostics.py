from __future__ import annotations

import ast
import builtins
import io
import json
import struct
import sys
import types
from pathlib import Path
from typing import Any, Self

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import staging_offline_verifier_diagnostics as diagnostics  # noqa: E402

sys.path.pop(0)

STATUS = b"""Name:\tsynthetic-probe
Uid:\t1000\t1000\t1000\t1000
CapInh:\t0000000000000000
CapPrm:\t0000000000000000
CapEff:\t0000000000000000
CapBnd:\t0000000000000000
CapAmb:\t0000000000000000
NoNewPrivs:\t1
Seccomp:\t2
"""
MOUNTS = b"23 1 0:20 / / ro,nosuid - overlay overlay rw\n24 23 0:21 / /tmp rw - tmpfs tmpfs rw\n"
NETDEV_HEADER = (
    b"Inter-| Receive | Transmit\n"
    b" face |bytes packets errs drop fifo frame compressed multicast|"
    b"bytes packets errs drop fifo colls carrier compressed\n"
)
NETDEV = NETDEV_HEADER + b" lo: " + b"0 " * 15 + b"0\n"


class View:
    def __init__(self) -> None:
        self.files: dict[str, Any] = {
            diagnostics.STATUS_PATH: STATUS,
            diagnostics.MOUNTS_PATH: MOUNTS,
            diagnostics.CGROUP_PATH: b"0::/synthetic-cgroup\n",
        }
        self.links: dict[str, Any] = {
            diagnostics.NETNS_PATH: "net:[10001]",
            diagnostics.PID1_NETNS_PATH: "net:[10000]",
        }
        self.interface_values: Any = [("lo", 0x8)]
        self.calls: list[str] = []

    def read(self, path: str) -> bytes:
        self.calls.append(path)
        value = self.files[path]
        if isinstance(value, Exception):
            raise value
        return value

    def readlink(self, path: str) -> str:
        self.calls.append(path)
        value = self.links[path]
        if isinstance(value, Exception):
            raise value
        return value

    def interfaces(self) -> list[tuple[str, int]]:
        self.calls.append("interfaces")
        if isinstance(self.interface_values, Exception):
            raise self.interface_values
        return self.interface_values


def test_favorable_facts_never_grant_acceptance() -> None:
    view = View()
    report = diagnostics._collect(view)
    assert report["diagnostic_only"] is True
    assert report["runtime_acceptance"] is False
    assert report["activation_authorized"] is False
    assert report["unproven_requirements"] == list(diagnostics.UNPROVEN)
    assert report["unavailable_observations"] == []
    assert all(value is True or value == 2 for value in report["observations"].values())
    assert set(view.calls) == (
        diagnostics.READ_PATHS - {diagnostics.NETDEV_PATH}
    ) | diagnostics.LINK_PATHS | {"interfaces"}
    serialized = json.dumps(report)
    assert "synthetic-cgroup" not in serialized
    assert "10001" not in serialized
    assert "1000" not in serialized


@pytest.mark.parametrize("position", range(4))
def test_any_root_uid_is_observed(position: int) -> None:
    values = ["1000"] * 4
    values[position] = "0"
    raw = STATUS.replace(b"1000\t1000\t1000\t1000", "\t".join(values).encode())
    assert (
        diagnostics._status(raw)["all_real_effective_saved_filesystem_uids_nonzero"]
        is False
    )


@pytest.mark.parametrize("field", diagnostics.CAPABILITY_FIELDS)
def test_each_capability_set_is_checked(field: str) -> None:
    raw = STATUS.replace(
        f"{field}:\t0000000000000000".encode(), f"{field}:\t0000000000000001".encode()
    )
    assert diagnostics._status(raw)["all_capability_sets_zero"] is False


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * (diagnostics.MAXIMUM_FILE_BYTES + 1),
        STATUS + b"Uid:\t1000 1000 1000 1000\n",
        STATUS.replace(b"NoNewPrivs:\t1", b"NoNewPrivs:\ttrue"),
        STATUS.replace(b"Uid:\t1000", b"Uid:\t-1"),
        STATUS.replace(b"Uid:\t1000", b"Uid:\t4294967296"),
        STATUS.replace(b"Uid:\t1000", b"Uid:\t01000"),
        STATUS.replace(b"CapEff:\t0000000000000000", b"CapEff:\t0"),
        STATUS.replace(b"CapAmb:\t0000000000000000\n", b""),
        STATUS.replace(b"Seccomp:\t2", b"Seccomp:\t3"),
        STATUS + b"\xff",
    ],
)
def test_invalid_status_is_unknown_not_favorable(raw: bytes) -> None:
    view = View()
    view.files[diagnostics.STATUS_PATH] = raw
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == ["status"]
    assert report["observations"]["all_capability_sets_zero"] is None
    assert report["runtime_acceptance"] is False


def test_no_new_privileges_and_seccomp_are_distinct() -> None:
    report = diagnostics._status(STATUS.replace(b"NoNewPrivs:\t1", b"NoNewPrivs:\t0"))
    assert report["no_new_privileges"] is False
    assert report["seccomp_mode_observed"] == 2
    assert (
        diagnostics._status(STATUS.replace(b"Seccomp:\t2\n", b""))[
            "seccomp_mode_observed"
        ]
        is None
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(MOUNTS, True), (MOUNTS.replace(b"/ / ro,nosuid", b"/ / rw,nosuid"), False)],
)
def test_root_mount_uses_mount_options_not_superblock(
    raw: bytes, expected: bool
) -> None:
    assert diagnostics._root_mount(raw) is expected


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not mountinfo\n",
        MOUNTS + MOUNTS,
        MOUNTS.replace(b"ro,nosuid", b"ro,rw"),
        MOUNTS.replace(b"ro,nosuid", b"nosuid"),
        MOUNTS.replace(b"/ / ro", b"/ /not-root ro"),
        MOUNTS.replace(b" - overlay overlay rw", b" - overlay overlay"),
    ],
)
def test_ambiguous_mounts_refuse(raw: bytes) -> None:
    with pytest.raises(
        diagnostics.DiagnosticsUnavailable, match="^invalid_observation$"
    ):
        diagnostics._root_mount(raw)


@pytest.mark.parametrize(
    ("values", "only_lo", "lo_down"),
    [
        ([("lo", 8)], True, True),
        ([("lo", 9)], True, False),
        ([("lo", 8), ("eth0", 0)], False, True),
        ([("eth0", 1)], False, None),
        ([], False, None),
    ],
)
def test_interface_facts_do_not_equate_down_with_absent(
    values: Any, only_lo: bool, lo_down: bool | None
) -> None:
    assert diagnostics._interfaces(values) == {
        "only_loopback_interface_observed": only_lo,
        "loopback_down_observed": lo_down,
    }


@pytest.mark.parametrize(
    ("values", "only_loopback", "loopback_down"),
    [
        ([("lo", 0)], False, None),
        ([("lo", 1)], False, None),
        ([("renamed0", 8)], True, True),
        ([("renamed0", 9)], True, False),
        ([("lo", 8), ("second0", 8)], False, None),
        ([("lo", 1), ("actual0", 8)], False, True),
        ([("actual0", 9), ("eth0", 0)], False, False),
    ],
)
def test_loopback_identity_uses_kernel_flag_not_name(
    values: Any, only_loopback: bool, loopback_down: bool | None
) -> None:
    assert diagnostics._interfaces(values) == {
        "only_loopback_interface_observed": only_loopback,
        "loopback_down_observed": loopback_down,
    }


def test_interface_named_lo_without_loopback_flag_is_not_positive() -> None:
    view = View()
    view.interface_values = [("lo", 0)]
    report = diagnostics._collect(view)
    assert report["observations"]["only_loopback_interface_observed"] is False
    assert report["observations"]["loopback_down_observed"] is None
    assert report["runtime_acceptance"] is False
    assert report["activation_authorized"] is False


@pytest.mark.parametrize(
    "values",
    [
        [("lo", True)],
        [("lo", -1)],
        [("lo", 65536)],
        [("lo", "8")],
        [("lo", 8), ("lo", 8)],
        [("../escape", 8)],
        [["lo", 8]],
        [("lo", 8)] * 65,
        None,
    ],
)
def test_malformed_interface_observations_refuse(values: Any) -> None:
    with pytest.raises(
        diagnostics.DiagnosticsUnavailable, match="^invalid_observation$"
    ):
        diagnostics._interfaces(values)


def test_pid1_comparison_is_not_namespace_origin_proof() -> None:
    view = View()
    view.links[diagnostics.PID1_NETNS_PATH] = view.links[diagnostics.NETNS_PATH]
    report = diagnostics._collect(view)
    assert report["observations"]["network_namespace_differs_from_pid1"] is False
    assert "trusted_outer_network_namespace_binding" in report["unproven_requirements"]


@pytest.mark.parametrize(
    "value", ["mnt:[1000]", "net:[-1]", "net:[1000]\n", "net:[" + "1" * 21 + "]", 1000]
)
def test_namespace_token_shape(value: Any) -> None:
    view = View()
    view.links[diagnostics.NETNS_PATH] = value
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == ["namespace_comparison"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(b"0::/\n", True), (b"0::/synthetic\n", True), (b"5:pids:/synthetic\n", False)],
)
def test_cgroup_membership_is_not_limit_or_kill_proof(
    raw: bytes, expected: bool
) -> None:
    assert diagnostics._cgroup(raw) is expected


@pytest.mark.parametrize(
    "raw", [b"", b"0::relative\n", b"0::/\n0::/second\n", b"0::/nul\x00\n"]
)
def test_invalid_cgroup_refuses(raw: bytes) -> None:
    with pytest.raises(diagnostics.DiagnosticsUnavailable):
        diagnostics._cgroup(raw)


def test_missing_kernel_access_is_sanitized_and_not_retried() -> None:
    view = View()
    secret_error = OSError("sensitive fixture path and values")
    for path in view.files:
        view.files[path] = secret_error
    view.links[diagnostics.NETNS_PATH] = secret_error
    view.interface_values = secret_error
    report = diagnostics._collect(view)
    assert len(report["unavailable_observations"]) == 5
    assert all(value is None for value in report["observations"].values())
    assert len(view.calls) == len(set(view.calls))
    assert "sensitive" not in json.dumps(report)


def test_non_linux_refuses_before_constructing_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diagnostics.sys, "platform", "darwin")

    def forbidden() -> None:
        pytest.fail("kernel view must not be created")

    monkeypatch.setattr(diagnostics, "_KernelView", forbidden)
    with pytest.raises(
        diagnostics.DiagnosticsUnavailable, match="^platform_unsupported$"
    ):
        diagnostics.capture_linux_verifier_diagnostics()


def test_kernel_file_reads_are_allowlisted_bounded_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Stream(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            calls.append(size)
            return super().read(size)

    def opened(path: str, mode: str) -> Stream:
        assert path == diagnostics.STATUS_PATH
        assert mode == "rb"
        return Stream(STATUS)

    monkeypatch.setattr("builtins.open", opened)
    assert diagnostics._KernelView().read(diagnostics.STATUS_PATH) == STATUS
    assert calls == [diagnostics.MAXIMUM_FILE_BYTES + 1]
    with pytest.raises(diagnostics.DiagnosticsUnavailable, match="^unsupported_path$"):
        diagnostics._KernelView().read("/not-allowed")
    with pytest.raises(diagnostics.DiagnosticsUnavailable, match="^unsupported_path$"):
        diagnostics._KernelView().readlink("/not-allowed")


def test_interface_reader_only_uses_mocked_local_ioctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Control:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append("closed")

        def fileno(self) -> int:
            return 42

    def control_socket(family: int, kind: int) -> Control:
        assert family == diagnostics.socket.AF_INET
        assert kind == diagnostics.socket.SOCK_DGRAM
        calls.append("socket")
        return Control()

    def ioctl(fd: int, operation: int, request: bytes) -> bytes:
        assert fd == 42 and operation == 0x8913
        assert request == struct.pack("256s", b"lo")
        calls.append("ioctl")
        return b"\x00" * 16 + struct.pack("@H", 8) + b"\x00" * 238

    def read_netdev(_view: object, path: str) -> bytes:
        assert path == diagnostics.NETDEV_PATH
        return NETDEV

    monkeypatch.setattr(diagnostics._KernelView, "read", read_netdev)
    monkeypatch.setattr(diagnostics.socket, "socket", control_socket)
    monkeypatch.setitem(sys.modules, "fcntl", types.SimpleNamespace(ioctl=ioctl))
    assert diagnostics._KernelView().interfaces() == [("lo", 8)]
    assert calls == ["socket", "ioctl", "closed"]


def test_only_fixed_probe_consumer_and_no_policy_wiring() -> None:
    source = ROOT / "scripts/staging_offline_verifier_diagnostics.py"
    fixed_probe = ROOT / "scripts/staging_runtime_capability_probe.py"
    assert b"staging_offline_verifier_diagnostics.py" in fixed_probe.read_bytes()
    tree = ast.parse(source.read_text())
    assert not any(isinstance(node, ast.If) for node in tree.body)
    dependency_inventory = ROOT / "scripts/canonical_staging_control_dependencies.json"
    inventory = json.loads(dependency_inventory.read_text())
    assert set(inventory) == {
        "control_inventory_sha256",
        "files",
        "schema",
        "selfExcluded",
        "sourceBase",
    }
    assert inventory["schema"] == "canonical_staging_audited_dependencies_v1"
    # The pinned JSON audit names source dependencies; it does not execute them.
    dependency = "scripts/staging_offline_verifier_diagnostics.py"
    assert inventory["files"].count(dependency) == 1
    inventory["files"].remove(dependency)
    assert "staging_offline_verifier_diagnostics" not in json.dumps(inventory)
    for directory in (ROOT / "scripts", ROOT / ".github/workflows"):
        for path in directory.iterdir():
            if (
                path not in {source, fixed_probe, dependency_inventory}
                and path.is_file()
            ):
                assert b"staging_offline_verifier_diagnostics" not in path.read_bytes()
    policy = json.loads(
        (
            ROOT / "contracts/staging-rehearsal/v1/restore-runtime-binding-policy.json"
        ).read_text()
    )
    assert policy["activation_ready"] is False
    assert not any(policy["activation"].values())


@pytest.mark.parametrize(
    "row",
    [
        b"x y z q / ro - overlay overlay rw\n",
        b"01 1 0:20 / / ro - overlay overlay rw\n",
        b"23 0 0:20 / / ro - overlay overlay rw\n",
        b"23 1 00:20 / / ro - overlay overlay rw\n",
        b"23 1 0:x / / ro - overlay overlay rw\n",
        b"23 1 0:20 relative / ro - overlay overlay rw\n",
        b"23 1 0:20 /bad\\999 / ro - overlay overlay rw\n",
        b"23 1 0:20 /bad\\0400\\ / ro - overlay overlay rw\n",
        b"23 1 0:20 / / ro,ro - overlay overlay rw\n",
        b"23 1 0:20 / / ro,,nosuid - overlay overlay rw\n",
        b"23 1 0:20 / / ro,foo= - overlay overlay rw\n",
        b"23 1 0:20 / / ro shared:x - overlay overlay rw\n",
        b"23 1 0:20 / / ro shared:1 shared:2 - overlay overlay rw\n",
        b"23 1 0:20 / / ro - overlay bad\\999 rw\n",
        b"23 1 0:20 / / ro - overlay overlay rw,rw\n",
        b"23 1 0:20 / / ro - overlay overlay rw\r\n",
        b"23 1 0:20 / / ro - overlay overlay rw",
        MOUNTS.replace(b"24 23", b"23 23"),
    ],
)
def test_review_malformed_mountinfo_is_unknown(row: bytes) -> None:
    view = View()
    view.files[diagnostics.MOUNTS_PATH] = row
    report = diagnostics._collect(view)
    assert report["observations"]["root_mount_read_only_observed"] is None
    assert report["unavailable_observations"] == ["mounts"]
    assert report["runtime_acceptance"] is False


def test_review_valid_encoded_mount_fields_remain_observable() -> None:
    raw = (
        b"23 1 0:20 /path\\040space\\134slash / ro,nosuid shared:7 "
        b"master:2 propagate_from:3 - fuse.example /dev/path\\040space rw,foo=bar\n"
        b"24 23 0:21 / /tmp rw - tmpfs tmpfs rw\n"
    )
    assert diagnostics._root_mount(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        b"0:pids:/x\n",
        b"5:pids:/x\n5:pids:/x\n",
        b"05:pids:/x\n",
        b"5::/x\n",
        b"5:pids,pids:/x\n",
        b"5:pids,:/x\n",
        b"5:pids:/x\n6:pids:/y\n",
        b"5:name=:/x\n",
        b"5:name=a,name=a:/x\n",
        b"5:pids:/x\r\n",
        b"5:pids:/x",
        b"0::/x\x7f\n",
    ],
)
def test_review_malformed_cgroup_is_unknown(raw: bytes) -> None:
    view = View()
    view.files[diagnostics.CGROUP_PATH] = raw
    report = diagnostics._collect(view)
    assert report["observations"]["unified_cgroup_membership_observed"] is None
    assert report["unavailable_observations"] == ["cgroup"]


def test_review_valid_hybrid_and_named_cgroups() -> None:
    assert diagnostics._cgroup(b"0::/\n5:cpu,cpuacct:/x\n7:name=systemd:/x\n") is True
    assert diagnostics._cgroup(b"5:pids:/x\n7:name=systemd:/x\n") is False


@pytest.mark.parametrize(
    "raw",
    [
        b"x" * (diagnostics.MAXIMUM_FILE_BYTES + 2),
        NETDEV_HEADER
        + b"".join(
            f" eth{index}: ".encode() + b"0 " * 15 + b"0\n" for index in range(65)
        ),
        NETDEV[:-1],
    ],
    ids=["oversized-bytes", "too-many-interfaces", "truncated-row"],
)
def test_review_interface_intake_is_bounded_and_overflow_unknown(
    raw: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = []

    class Stream(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            reads.append(size)
            return super().read(size)

    def opened(path: str, mode: str) -> Stream:
        assert path == "/proc/thread-self/net/dev" and mode == "rb"
        return Stream(raw)

    def forbidden(*_args: object) -> Any:
        pytest.fail("overflow must refuse without unbounded enumeration or socket")

    monkeypatch.setattr(builtins, "open", opened)
    monkeypatch.setattr(diagnostics.socket, "if_nameindex", forbidden)
    monkeypatch.setattr(diagnostics.socket, "socket", forbidden)
    view = View()
    monkeypatch.setattr(view, "interfaces", diagnostics._KernelView().interfaces)
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == ["interfaces"]
    assert report["observations"]["only_loopback_interface_observed"] is None
    assert reads == [diagnostics.MAXIMUM_FILE_BYTES + 1]


def test_review_unsupported_import_without_fcntl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def importing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fcntl":
            raise ModuleNotFoundError("synthetic fcntl unavailable")
        return original_import(name, *args, **kwargs)

    module = types.ModuleType("diagnostics_unsupported_platform_fixture")
    source = (ROOT / "scripts/staging_offline_verifier_diagnostics.py").read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", importing)
        patch.setattr(sys, "platform", "win32")
        exec(compile(source, "unsupported_fixture", "exec"), module.__dict__)
        with pytest.raises(
            module.DiagnosticsUnavailable, match="^platform_unsupported$"
        ):
            module.capture_linux_verifier_diagnostics()


@pytest.mark.parametrize("access", [b"ro=1", b"rw=1"])
def test_review_access_options_cannot_take_values(access: bytes) -> None:
    with pytest.raises(diagnostics.DiagnosticsUnavailable):
        diagnostics._root_mount(b"23 1 0:20 / / " + access + b" - overlay overlay rw\n")


@pytest.mark.parametrize(
    "raw",
    [
        NETDEV.replace(b"Receive", b"Wrong"),
        NETDEV.replace(b"colls", b"wrong"),
        NETDEV.replace(b" lo:", b" ../escape:"),
        NETDEV.replace(b"0 ", b"00 ", 1),
        NETDEV.replace(b"0 ", b"-1 ", 1),
        NETDEV.replace(b"0 ", b"18446744073709551616 ", 1),
        NETDEV.replace(b"0 ", b"", 1),
        NETDEV + NETDEV.splitlines(keepends=True)[-1],
        NETDEV + b"\n",
    ],
    ids=[
        "heading",
        "counter-heading",
        "name",
        "leading-zero",
        "negative",
        "overflow",
        "count",
        "duplicate",
        "blank",
    ],
)
def test_review_netdev_malformed_names_or_counters_refuse(raw: bytes) -> None:
    with pytest.raises(diagnostics.DiagnosticsUnavailable):
        diagnostics._interface_names(raw)


def test_review_netdev_maximum_valid_intake() -> None:
    raw = NETDEV_HEADER + b"".join(
        f" eth{index}: ".encode() + b"0 " * 15 + b"0\n" for index in range(64)
    )
    assert diagnostics._interface_names(raw) == [f"eth{index}" for index in range(64)]


def test_review_cgroup_cannot_have_multiple_hierarchy_names() -> None:
    with pytest.raises(diagnostics.DiagnosticsUnavailable):
        diagnostics._cgroup(b"5:name=one,name=two:/x\n")


STATUS_OBSERVATIONS = (
    "all_real_effective_saved_filesystem_uids_nonzero",
    "all_capability_sets_zero",
    "no_new_privileges",
    "seccomp_mode_observed",
)


@pytest.mark.parametrize("control", [*range(9), *range(11, 32), 127])
def test_review18_status_disallowed_controls_are_unknown(control: int) -> None:
    view = View()
    view.files[diagnostics.STATUS_PATH] = STATUS.replace(
        b"synthetic-probe", b"synthetic" + bytes([control]) + b"-probe"
    )
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == ["status"]
    assert all(report["observations"][key] is None for key in STATUS_OBSERVATIONS)
    assert report["runtime_acceptance"] is False


@pytest.mark.parametrize(
    "raw",
    [
        STATUS[:-1],
        STATUS.replace(b"\n", b"\r\n"),
        STATUS + b"\n",
        STATUS + b" \t\n",
        STATUS + b"broken\n",
        STATUS + b"Name:\textra\n",
        STATUS + b"bad key:\tvalue\n",
        STATUS + b"Extra:\tvalue\nExtra:\tvalue\n",
    ],
    ids=[
        "truncated",
        "crlf",
        "blank",
        "whitespace-blank",
        "unframed",
        "duplicate-name",
        "invalid-key",
        "duplicate-other",
    ],
)
def test_review18_status_record_framing_is_unknown(raw: bytes) -> None:
    view = View()
    view.files[diagnostics.STATUS_PATH] = raw
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == ["status"]
    assert all(report["observations"][key] is None for key in STATUS_OBSERVATIONS)


def test_review18_status_tabs_and_unique_unobserved_fields_remain_valid() -> None:
    observed = diagnostics._status(STATUS + b"Extra_1:\tvalue\n")
    assert observed["all_real_effective_saved_filesystem_uids_nonzero"] is True
    assert observed["all_capability_sets_zero"] is True
    assert observed["no_new_privileges"] is True
    assert observed["seccomp_mode_observed"] == 2


@pytest.mark.parametrize("path", [diagnostics.NETNS_PATH, diagnostics.PID1_NETNS_PATH])
@pytest.mark.parametrize(
    "value", ["net:[01]", "net:[0]", "net:[4294967296]", "net:[18446744073709551615]"]
)
def test_review18_namespace_canonical_uint32_bounds(path: str, value: str) -> None:
    view = View()
    view.links[path] = value
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == ["namespace_comparison"]
    assert report["observations"]["network_namespace_differs_from_pid1"] is None


@pytest.mark.parametrize(
    "pid1,expected", [("net:[1]", True), ("net:[4294967295]", False)]
)
def test_review18_namespace_positive_uint32_boundaries(
    pid1: str, expected: bool
) -> None:
    view = View()
    view.links[diagnostics.NETNS_PATH] = "net:[4294967295]"
    view.links[diagnostics.PID1_NETNS_PATH] = pid1
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == []
    assert report["observations"]["network_namespace_differs_from_pid1"] is expected
    assert report["runtime_acceptance"] is False


@pytest.mark.parametrize("prefix", [b"", b"0::/\n"])
def test_review18_named_cgroup_before_controller_is_noncanonical(prefix: bytes) -> None:
    raw = prefix + b"5:name=example,pids:/x\n"
    with pytest.raises(diagnostics.DiagnosticsUnavailable):
        diagnostics._cgroup(raw)
    view = View()
    view.files[diagnostics.CGROUP_PATH] = raw
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == ["cgroup"]
    assert report["observations"]["unified_cgroup_membership_observed"] is None


@pytest.mark.parametrize("prefix,expected", [(b"", False), (b"0::/\n", True)])
def test_review18_kernel_ordered_named_cgroup_with_controller_is_valid(
    prefix: bytes, expected: bool
) -> None:
    # Linux proc_cgroup_show emits controllers, then optional name=, in one field.
    raw = prefix + b"5:pids,name=example:/x\n"
    assert diagnostics._cgroup(raw) is expected
    view = View()
    view.files[diagnostics.CGROUP_PATH] = raw
    report = diagnostics._collect(view)
    assert report["unavailable_observations"] == []
    assert report["observations"]["unified_cgroup_membership_observed"] is expected
    assert report["runtime_acceptance"] is False
