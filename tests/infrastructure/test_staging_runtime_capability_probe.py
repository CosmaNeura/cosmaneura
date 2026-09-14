"""Synthetic mocks ONLY: never fork, create namespaces or execute the probe."""

from __future__ import annotations

import ast
import contextlib
import errno
import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import staging_runtime_capability_probe as probe  # noqa: E402

sys.path.pop(0)


class Kernel:
    def __init__(self, failure: int = -1) -> None:
        self.calls: list[tuple[int | str, ...]] = []
        self.namespaces = [(4, 10), (4, 11), (4, 12)]
        self.failure = failure

    def prctl(self, option: int, argument: int) -> None:
        self.calls.append(("prctl", option, argument))

    def unshare(self, flag: int) -> None:
        self.calls.append(("unshare", flag))
        if flag == self.failure:
            raise PermissionError(errno.EPERM, "synthetic-private-error")
        index = {
            probe.CLONE_NEWUSER: 0,
            probe.CLONE_NEWNET: 1,
            probe.CLONE_NEWNS: 2,
        }.get(flag)
        if index is not None:
            self.namespaces[index] = (4, 100 + index)

    def drop_capabilities(self) -> None:
        self.calls.append(("drop_capabilities",))


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> Any:
    kernel = Kernel()
    network: dict[str, Any] = {
        "only_loopback_interface_observed": True,
        "loopback_down_observed": True,
    }
    status: dict[str, Any] = {
        "all_real_effective_saved_filesystem_uids_nonzero": True,
        "all_capability_sets_zero": True,
        "no_new_privileges": True,
        "seccomp_mode_observed": 2,
    }
    diagnostic = SimpleNamespace(
        STATUS_PATH="synthetic-status",
        _KernelView=lambda: SimpleNamespace(
            interfaces=lambda: [], read=lambda path: b"synthetic"
        ),
        _interfaces=lambda values: network,
        _status=lambda raw: status,
    )
    monkeypatch.setattr(probe, "_identity", lambda: None)
    monkeypatch.setattr(probe, "_map_identity", lambda: None)
    monkeypatch.setattr(probe, "_diagnostic_module", lambda: diagnostic)
    monkeypatch.setattr(probe, "_namespace", lambda: tuple(kernel.namespaces))
    return SimpleNamespace(kernel=kernel, network=network, status=status)


def test_complete_prerequisites_never_mean_acceptance(environment: Any) -> None:
    result = probe._probe(environment.kernel)
    assert result == probe._result(len(probe.PHASES), None)
    assert result["diagnostic_only"] is True
    assert result["runtime_acceptance"] is False
    assert result["activation_authorized"] is False
    assert "read_only_root_and_all_writable_mounts" in result["unproven_requirements"]
    assert environment.kernel.calls == [
        ("prctl", 38, 1),
        ("unshare", probe.CLONE_NEWUSER),
        ("unshare", probe.CLONE_NEWNET),
        ("unshare", probe.CLONE_NEWNS),
        ("unshare", probe.CLONE_NEWPID),
        ("drop_capabilities",),
    ]


@pytest.mark.parametrize(
    ("flag", "phase"),
    [
        (probe.CLONE_NEWUSER, "user_namespace"),
        (probe.CLONE_NEWNET, "network_namespace"),
        (probe.CLONE_NEWNS, "mount_namespace"),
        (probe.CLONE_NEWPID, "pid_namespace_request"),
    ],
)
def test_first_denial_stops_without_fallback(
    environment: Any, flag: int, phase: str
) -> None:
    environment.kernel.failure = flag
    result = probe._probe(environment.kernel)
    assert result["failed_phase"] == phase
    assert result["reason"] == "denied"
    assert environment.kernel.calls[-1] == ("unshare", flag)
    assert "synthetic-private-error" not in json.dumps(result)


@pytest.mark.parametrize("index", range(3))
def test_unchanged_namespace_cannot_pass(
    environment: Any, monkeypatch: pytest.MonkeyPatch, index: int
) -> None:
    before = tuple(environment.kernel.namespaces)

    def snapshot() -> tuple[tuple[int, int], ...]:
        values = environment.kernel.namespaces.copy()
        values[index] = before[index]
        return tuple(values)

    monkeypatch.setattr(probe, "_namespace", snapshot)
    result = probe._probe(environment.kernel)
    assert (
        result["failed_phase"]
        == ("user_namespace", "network_namespace", "mount_namespace")[index]
    )
    assert result["reason"] == "verification_failed"


@pytest.mark.parametrize(
    "key", ["only_loopback_interface_observed", "loopback_down_observed"]
)
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_network_requires_exact_true(environment: Any, key: str, value: Any) -> None:
    environment.network[key] = value
    result = probe._probe(environment.kernel)
    assert result["failed_phase"] == "network_interfaces"
    assert result["reason"] == "verification_failed"


@pytest.mark.parametrize(
    "key",
    [
        "all_real_effective_saved_filesystem_uids_nonzero",
        "all_capability_sets_zero",
        "no_new_privileges",
    ],
)
@pytest.mark.parametrize("value", [False, None, 1])
def test_final_status_requires_exact_true(
    environment: Any, key: str, value: Any
) -> None:
    environment.status[key] = value
    assert probe._probe(environment.kernel)["failed_phase"] == "final_status"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (probe.ProbeRefused(), "verification_failed"),
        (OSError(errno.EACCES, "private"), "denied"),
        (OSError(errno.ENOSYS, "private"), "unavailable"),
        (OSError(errno.EINVAL, "private"), "unavailable"),
        (OSError(errno.ENOENT, "private"), "unavailable"),
        (OSError(errno.ENOMEM, "private"), "resource_limit"),
        (OSError(errno.EAGAIN, "private"), "resource_limit"),
        (OSError(errno.ENOSPC, "private"), "resource_limit"),
        (OSError(errno.EMFILE, "private"), "resource_limit"),
        (OSError(errno.EIO, "private"), "internal"),
        (ValueError("private"), "internal"),
    ],
)
def test_error_redaction(error: Exception, reason: str) -> None:
    assert probe._reason(error) == reason


@pytest.mark.parametrize("phase", range(len(probe.PHASES)))
@pytest.mark.parametrize("reason", sorted(probe.REASONS))
def test_fixed_outcomes_roundtrip(phase: int, reason: str) -> None:
    value = probe._result(phase, reason)
    assert probe._validate(json.dumps(value).encode()) == value


def test_positive_outcome_roundtrip() -> None:
    value = probe._result(len(probe.PHASES), None)
    assert probe._validate(json.dumps(value).encode()) == value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("activation_authorized", True),
        ("runtime_acceptance", True),
        ("diagnostic_only", 1),
        ("schema_version", True),
        ("unproven_requirements", []),
        ("completed_phases", ["invented"]),
        ("failed_phase", "private-uid"),
        ("extra", "private-output"),
        ("reason", "invented"),
    ],
)
def test_child_cannot_inflate_or_extend_report(key: str, value: Any) -> None:
    report = probe._result(len(probe.PHASES), None)
    report[key] = value
    with pytest.raises(probe.ProbeRefused):
        probe._validate(json.dumps(report).encode())


@pytest.mark.parametrize("raw", [b"", b"x" * 4096, b"null", b"[]", b"{}"])
def test_invalid_report(raw: bytes) -> None:
    with pytest.raises((probe.ProbeRefused, ValueError)):
        probe._validate(raw)


def test_receive_is_bounded_and_never_reads_after_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(probe.time, "monotonic", lambda: 10)
    monkeypatch.setattr(probe.select, "select", lambda *args: ([8], [], []))

    def read(fd: int, size: int) -> bytes:
        calls.append((fd, size))
        return b"x" * size

    monkeypatch.setattr(probe.os, "read", read)
    with pytest.raises(probe.ProbeRefused):
        probe._receive(8, 11)
    assert calls == [(8, 4096)]


@pytest.mark.parametrize("timeout", [True, False])
def test_receive_deadline(monkeypatch: pytest.MonkeyPatch, timeout: bool) -> None:
    monkeypatch.setattr(probe.time, "monotonic", lambda: 12 if timeout else 10)
    monkeypatch.setattr(probe.select, "select", lambda *args: ([], [], []))
    with pytest.raises(probe.ProbeRefused):
        probe._receive(8, 11)


def test_receive_fragmented_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = iter([b"first", b"second", b""])
    monkeypatch.setattr(probe.time, "monotonic", lambda: 10)
    monkeypatch.setattr(probe.select, "select", lambda *args: ([8], [], []))
    monkeypatch.setattr(probe.os, "read", lambda *args: next(chunks))
    assert probe._receive(8, 11) == b"firstsecond"


def test_mapping_is_exact_and_every_descriptor_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(probe, "_identity", lambda: calls.append("identity"))
    monkeypatch.setattr(
        probe.os, "open", lambda path, flags: calls.append((path, flags)) or 9
    )
    monkeypatch.setattr(
        probe.os, "write", lambda fd, data: calls.append((fd, data)) or len(data)
    )
    monkeypatch.setattr(probe.os, "close", lambda fd: calls.append(("close", fd)))
    probe._map_identity()
    assert [call for call in calls if isinstance(call, tuple) and call[0] == 9] == [
        (9, b"65532 65532 1\n"),
        (9, b"deny\n"),
        (9, b"65532 65532 1\n"),
    ]
    assert calls.count(("close", 9)) == 3
    assert calls[-1] == "identity"


def test_mapping_short_write_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = []
    monkeypatch.setattr(probe.os, "open", lambda *args: 9)
    monkeypatch.setattr(probe.os, "write", lambda *args: 0)
    monkeypatch.setattr(probe.os, "close", closed.append)
    with pytest.raises(probe.ProbeRefused):
        probe._map_identity()
    assert closed == [9]


@pytest.mark.parametrize(
    "raw", [b"64\n", b"-1\n", b"40", b"40\nextra", b"999999\n", b"\n"]
)
def test_unknown_capability_range_stops(
    monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    monkeypatch.setattr("builtins.open", lambda *args: io.BytesIO(raw))
    kernel = probe._Linux.__new__(probe._Linux)
    with pytest.raises(probe.ProbeRefused):
        kernel.drop_capabilities()


def test_capability_drop_uses_only_private_self_and_zero_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr("builtins.open", lambda *args: io.BytesIO(b"40\n"))
    kernel = probe._Linux.__new__(probe._Linux)
    monkeypatch.setattr(kernel, "prctl", lambda *args: calls.append(args))

    def syscall(number: int, header: Any, values: Any) -> int:
        assert number == 126
        assert header._obj.version == 0x20080522
        assert header._obj.pid == 0
        assert bytes(values) == b"\0" * 24
        calls.append("capset")
        return 0

    kernel.lib = SimpleNamespace(syscall=syscall)  # type: ignore[assignment]
    kernel.drop_capabilities()
    assert calls == [(24, index) for index in range(41)] + [(47, 4), "capset"]


def test_no_import_time_execution_or_other_consumers() -> None:
    tree = ast.parse(Path(probe.__file__).read_text())
    forbidden = {
        "Popen",
        "run",
        "execve",
        "system",
        "connect",
        "send",
        "mount",
        "setns",
    }
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden
        for node in ast.walk(tree)
    )
    assert (
        sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fork"
            for node in ast.walk(tree)
        )
        == 1
    )
    dockerfile = (ROOT / "infra/runtime_capability/Dockerfile").read_text()
    assert "\nRUN " not in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert '"-I", "-S", "-B"' in dockerfile
    assert (
        "@sha256:5e511b0e940def74dd23389510300c76017ca9bbc92484c40cff0951adf32245"
        in dockerfile
    )


def test_missing_network_fields_fail(environment: Any) -> None:
    environment.network.clear()
    assert probe._probe(environment.kernel)["failed_phase"] == "network_interfaces"


def test_close_inherited_does_not_trust_rlimit(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = []
    entries = [SimpleNamespace(name=str(fd)) for fd in [0, 1, 2, 3, 500000]]
    monkeypatch.setattr(probe.os, "fstat", lambda fd: None)
    monkeypatch.setattr(
        probe.os, "scandir", lambda path: contextlib.nullcontext(entries)
    )
    monkeypatch.setattr(probe.os, "close", closed.append)
    probe._close_inherited()
    assert closed == [3, 500000]


@pytest.mark.parametrize("names", [["3"] * 257, ["2147483648"], ["secret"], ["٣"]])
def test_close_inherited_rejects_unbounded_or_invalid_listing(
    monkeypatch: pytest.MonkeyPatch, names: list[str]
) -> None:
    entries = [SimpleNamespace(name=name) for name in names]
    monkeypatch.setattr(probe.os, "fstat", lambda fd: None)
    monkeypatch.setattr(
        probe.os, "scandir", lambda path: contextlib.nullcontext(entries)
    )
    with pytest.raises(probe.ProbeRefused):
        probe._close_inherited()


@pytest.mark.parametrize("number", [errno.EBADF, errno.EIO])
def test_close_inherited_only_tolerates_already_closed_scanner(
    monkeypatch: pytest.MonkeyPatch, number: int
) -> None:
    monkeypatch.setattr(probe.os, "fstat", lambda fd: None)
    monkeypatch.setattr(
        probe.os,
        "scandir",
        lambda path: contextlib.nullcontext([SimpleNamespace(name="3")]),
    )

    def close(fd: int) -> None:
        raise OSError(number, "private")

    monkeypatch.setattr(probe.os, "close", close)
    if number == errno.EBADF:
        probe._close_inherited()
    else:
        with pytest.raises(OSError):
            probe._close_inherited()


@pytest.fixture
def parent(monkeypatch: pytest.MonkeyPatch) -> Any:
    calls: list[Any] = []
    monkeypatch.setattr(probe, "_identity", lambda: calls.append("identity"))
    monkeypatch.setattr(
        probe, "_close_inherited", lambda: calls.append("close_inherited")
    )
    monkeypatch.setattr(probe.os, "pipe2", lambda flags: (8, 9), raising=False)
    monkeypatch.setattr(probe.os, "getpid", lambda: 100)
    monkeypatch.setattr(probe.os, "fork", lambda: calls.append("fork") or 101)
    monkeypatch.setattr(probe.os, "close", lambda fd: calls.append(("close", fd)))
    monkeypatch.setattr(
        probe.os, "kill", lambda pid, sig: calls.append(("kill", pid, sig))
    )
    monkeypatch.setattr(
        probe.os,
        "waitpid",
        lambda pid, flags: calls.append(("wait", pid, flags)) or (pid, 0),
    )
    monkeypatch.setattr(probe.time, "monotonic", lambda: 10)
    report = probe._result(len(probe.PHASES), None)
    monkeypatch.setattr(
        probe, "_receive", lambda fd, deadline: json.dumps(report).encode()
    )
    return SimpleNamespace(calls=calls, report=report)


def test_parent_closes_before_fork_and_requires_clean_exit(parent: Any) -> None:
    assert probe._run() == parent.report
    assert parent.calls[:3] == ["identity", "close_inherited", "fork"]
    assert parent.calls[-1] == ("close", 8)
    assert ("close", 9) in parent.calls
    assert not any(
        isinstance(call, tuple) and call[0] == "kill" for call in parent.calls
    )


def test_parent_bad_report_kills_exact_child(
    parent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe, "_receive", lambda fd, deadline: b"{}")
    with pytest.raises(probe.ProbeRefused):
        probe._run()
    assert ("kill", 101, probe.signal.SIGKILL) in parent.calls
    assert ("wait", 101, probe.os.WNOHANG) in parent.calls
    assert parent.calls[-1] == ("close", 8)


def test_parent_nonzero_child_is_not_accepted(
    parent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe.os, "waitpid", lambda pid, flags: (pid, 256))
    with pytest.raises(probe.ProbeRefused):
        probe._run()


def test_parent_timeout_after_output_kills_child(
    parent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = iter([10, 40])
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(values))
    with pytest.raises(probe.ProbeRefused):
        probe._run()
    assert ("kill", 101, probe.signal.SIGKILL) in parent.calls


def test_failed_fork_closes_both_pipe_ends(
    parent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fork() -> int:
        raise OSError(errno.EAGAIN, "synthetic")

    monkeypatch.setattr(probe.os, "fork", fork)
    with pytest.raises(OSError):
        probe._run()
    assert parent.calls[-2:] == [("close", 8), ("close", 9)]


@pytest.mark.parametrize("outcome", ["positive", "negative", "bad_write"])
def test_main_fixed_exit_and_bounded_nonblocking_output(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    report = (
        probe._result(len(probe.PHASES), None)
        if outcome != "negative"
        else probe._result(2, "denied")
    )
    writes = []
    monkeypatch.setattr(probe.sys, "argv", ["probe.py"])
    monkeypatch.setattr(probe, "_run", lambda: report)
    monkeypatch.setattr(
        probe.os, "set_blocking", lambda fd, value: writes.append((fd, value))
    )
    monkeypatch.setattr(
        probe.os,
        "write",
        lambda fd, data: writes.append((fd, data))
        or (0 if outcome == "bad_write" else len(data)),
    )
    assert probe.main() == {"positive": 0, "negative": 2, "bad_write": 3}[outcome]
    assert writes[0] == (1, False)
    assert len(writes[1][1]) < probe.MAX_REPORT_BYTES


def test_main_refuses_arguments_before_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe.sys, "argv", ["probe.py", "--override"])
    monkeypatch.setattr(probe, "_run", lambda: pytest.fail("must not run"))
    assert probe.main() == 3


@pytest.mark.parametrize(
    "wrong", ["none", "platform", "architecture", "uid", "gid", "groups"]
)
def test_exact_execution_identity(monkeypatch: pytest.MonkeyPatch, wrong: str) -> None:
    monkeypatch.setattr(
        probe.sys, "platform", "linux" if wrong != "platform" else "darwin"
    )
    monkeypatch.setattr(
        probe.platform,
        "machine",
        lambda: "aarch64" if wrong == "architecture" else "x86_64",
    )
    monkeypatch.setattr(
        probe.os,
        "getresuid",
        lambda: (65532, 0, 65532) if wrong == "uid" else (65532,) * 3,
        raising=False,
    )
    monkeypatch.setattr(
        probe.os,
        "getresgid",
        lambda: (65532, 0, 65532) if wrong == "gid" else (65532,) * 3,
        raising=False,
    )
    monkeypatch.setattr(
        probe.os, "getgroups", lambda: [65532, 7] if wrong == "groups" else []
    )
    if wrong == "none":
        probe._identity()
    else:
        with pytest.raises(probe.ProbeRefused):
            probe._identity()


@pytest.mark.parametrize("failure", ["none", "parent_race", "short_write", "setup"])
def test_child_setup_and_single_exit(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import resource

    calls: list[Any] = []
    kernel = Kernel()
    monkeypatch.setattr(probe, "_Linux", lambda: kernel)
    monkeypatch.setattr(
        probe,
        "_probe",
        lambda selected: calls.append("probe")
        or probe._result(len(probe.PHASES), None),
    )
    monkeypatch.setattr(
        probe.os, "getppid", lambda: 200 if failure == "parent_race" else 100
    )
    monkeypatch.setattr(probe.os, "environ", {"SYNTHETIC_TOKEN": "not-real"})
    monkeypatch.setattr(probe.os, "close", lambda fd: calls.append(("close", fd)))
    monkeypatch.setattr(
        probe.os,
        "write",
        lambda fd, raw: calls.append(("write", fd, raw))
        or (0 if failure == "short_write" else len(raw)),
    )

    def limit(kind: int, values: tuple[int, int]) -> None:
        calls.append(("limit", kind, values))
        if failure == "setup":
            raise OSError(errno.EPERM, "private")

    def finish(code: int) -> None:
        calls.append(("exit", code))
        raise SystemExit(code)

    monkeypatch.setattr(resource, "setrlimit", limit)
    monkeypatch.setattr(probe.os, "_exit", finish)
    with pytest.raises(SystemExit) as error:
        probe._child(9, 100)
    assert error.value.code == (0 if failure == "none" else 3)
    assert calls[:3] == [("close", 0), ("close", 1), ("close", 2)]
    assert probe.os.environ == {}
    assert (
        len([call for call in calls if isinstance(call, tuple) and call[0] == "exit"])
        == 1
    )
    if failure in {"parent_race", "setup"}:
        assert "probe" not in calls
        assert not any(isinstance(call, tuple) and call[0] == "write" for call in calls)
    else:
        assert kernel.calls == [("prctl", 1, probe.signal.SIGKILL)]
        assert ("limit", resource.RLIMIT_CPU, (5, 5)) in calls
        assert ("limit", resource.RLIMIT_AS, (134217728, 134217728)) in calls
        assert ("limit", resource.RLIMIT_CORE, (0, 0)) in calls
        assert ("limit", resource.RLIMIT_FSIZE, (0, 0)) in calls
        assert (
            len(
                [
                    call
                    for call in calls
                    if isinstance(call, tuple) and call[0] == "write"
                ]
            )
            == 1
        )


def test_image_candidate_binds_current_source_without_acceptance() -> None:
    candidate = json.loads(
        (ROOT / "infra/runtime_capability/image-candidate.json").read_text()
    )
    assert candidate["diagnostic_only"] is True
    assert candidate["activation_authorized"] is False
    inspection = candidate["inspection"]
    assert inspection["image_executed"] is False
    assert inspection["image_published"] is False
    assert inspection["runtime_acceptance"] is False
    assert inspection["architecture"] == "amd64" and inspection["os"] == "linux"
    for name in (
        "staging_runtime_capability_probe.py",
        "staging_offline_verifier_diagnostics.py",
    ):
        raw = (ROOT / "scripts" / name).read_bytes()
        expected = inspection["files"]["probe/" + name]
        assert expected["sha256"] == hashlib.sha256(raw).hexdigest()
        assert expected["bytes"] == len(raw)
        assert expected["uid"] == expected["gid"] == 0
        assert not int(expected["mode"], 8) & 0o022
    assert (
        candidate["dockerfile_sha256"]
        == hashlib.sha256(
            (ROOT / "infra/runtime_capability/Dockerfile").read_bytes()
        ).hexdigest()
    )
