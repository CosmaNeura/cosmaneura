from __future__ import annotations

import ast
import io
import json
import struct
import sys
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
    assert set(view.calls) == diagnostics.READ_PATHS | diagnostics.LINK_PATHS | {
        "interfaces"
    }
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

    monkeypatch.setattr(diagnostics.socket, "if_nameindex", lambda: [(1, "lo")])
    monkeypatch.setattr(diagnostics.socket, "socket", control_socket)
    monkeypatch.setattr(diagnostics.fcntl, "ioctl", ioctl)
    assert diagnostics._KernelView().interfaces() == [("lo", 8)]
    assert calls == ["socket", "ioctl", "closed"]


def test_no_cli_or_policy_consumer_wiring() -> None:
    source = ROOT / "scripts/staging_offline_verifier_diagnostics.py"
    tree = ast.parse(source.read_text())
    assert not any(isinstance(node, ast.If) for node in tree.body)
    for directory in (ROOT / "scripts", ROOT / ".github/workflows"):
        for path in directory.iterdir():
            if path != source and path.is_file():
                assert b"staging_offline_verifier_diagnostics" not in path.read_bytes()
    policy = json.loads(
        (
            ROOT / "contracts/staging-rehearsal/v1/restore-runtime-binding-policy.json"
        ).read_text()
    )
    assert policy["activation_ready"] is False
    assert not any(policy["activation"].values())
