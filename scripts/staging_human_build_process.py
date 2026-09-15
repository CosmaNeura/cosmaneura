"""Bounded credential-free build subprocesses; no CLI, Docker or authority discovery.

Lifecycle logic derives from the independently reviewed snapshot child owner
(PR397 6d5eb606). This copy does not import or modify the sealed snapshot runtime.
Only an explicitly authorized, reviewed build controller supplies build commands.
"""

from __future__ import annotations

import math
import os
import re
import resource
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

MAX_BYTES = 16 * 1024**3
CHUNK = 1024 * 1024


class BuildProcessRefused(RuntimeError):
    """Content-free failure; never accept or retry an ambiguous child."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise BuildProcessRefused(code)


@contextmanager
def defer_control_signals() -> Iterator[None]:
    require(threading.current_thread() is threading.main_thread(), "build_main_thread")
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGALRM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextmanager
def build_deadline(seconds: int) -> Iterator[tuple[float, Callable[[], None]]]:
    """Owned one-shot timer; pending-alarm teardown follows reviewed PR414."""
    require(type(seconds) is int and 0 < seconds <= 3600, "build_deadline")
    require(threading.current_thread() is threading.main_thread(), "build_main_thread")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "build_caller_timer")
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    require(signal.SIGALRM not in original_mask, "build_caller_timer")
    require(signal.SIGALRM not in signal.sigpending(), "build_caller_timer")
    started, wall = time.monotonic(), time.time()
    handler = signal.getsignal(signal.SIGALRM)
    fired = [False]
    closing = False

    def tick() -> None:
        elapsed = time.monotonic() - started
        require(not fired[0] and 0 <= elapsed < seconds, "build_deadline")
        require(abs((time.time() - wall) - elapsed) <= 5, "build_clock_changed")

    def expired(_signal, _frame):
        fired[0] = True
        if not closing:
            raise BuildProcessRefused("build_deadline")

    try:
        with defer_control_signals():
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, seconds)
        tick()
        yield started + seconds, tick
        tick()
        require(signal.getsignal(signal.SIGALRM) is expired, "build_timer_changed")
    finally:
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


class BuildProcesses:
    """One process group at a time; fixed caller commands, no shell or retries."""

    def __init__(self, deadline: float, revalidate_runtime: Callable[[], None]):
        require(
            math.isfinite(deadline) and deadline > time.monotonic(), "build_deadline"
        )
        self.deadline = deadline
        self.revalidate_runtime = revalidate_runtime
        self.child: subprocess.Popen[bytes] | None = None
        self.cleanup_failed = False
        self.may_signal_child = False
        self.cleanup_deadline: float | None = None

    def remaining(self) -> float:
        value = self.deadline - time.monotonic()
        require(value > 0, "build_deadline")
        return value

    def group_members(
        self, process_id: int, *, deadline: float | None = None
    ) -> tuple[tuple[int, str], ...]:
        # On macOS killpg(..., 0) can report EPERM for a zombie-only group.
        # Exact read-only ps selection distinguishes that from active children;
        # denial, malformed output or an unknown state is never called absence.
        remaining = 2 if deadline is None else min(2, deadline - time.monotonic())
        require(remaining > 0, "build_group_readback")
        result = subprocess.run(
            ["/bin/ps", "-g", str(process_id), "-o", "pid=,pgid=,stat="],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=remaining,
            check=False,
        )
        require(len(result.stdout) <= 16384, "build_group_readback")
        if result.returncode == 1 and not result.stdout.strip():
            return ()
        require(
            result.returncode == 0 and result.stdout.strip(), "build_group_readback"
        )
        members = []
        for line in result.stdout.decode("ascii").splitlines():
            fields = line.split()
            require(
                len(fields) == 3
                and all(value.isdigit() for value in fields[:2])
                and int(fields[1]) == process_id
                and re.fullmatch(r"[A-Z][A-Za-z+<>s]*", fields[2]),
                "build_group_readback",
            )
            members.append((int(fields[0]), fields[2]))
        return tuple(members)

    def cancel_and_reap(self) -> None:
        with defer_control_signals():
            self._cancel_and_reap()

    def _cancel_and_reap(self) -> None:
        child = self.child
        if child is None:
            require(not self.cleanup_failed, "build_group_not_gone")
            return
        if child.returncode is not None:
            self.may_signal_child = False
            self.cleanup_failed = True
            self.child = None
            if child.stdout is not None:
                child.stdout.close()
            raise BuildProcessRefused("build_child_already_reaped")
        if self.cleanup_deadline is None:
            self.cleanup_deadline = time.monotonic() + 5
        cleanup_deadline = self.cleanup_deadline

        def remaining_cleanup() -> float:
            remaining = cleanup_deadline - time.monotonic()
            require(remaining > 0, "build_group_not_gone")
            return remaining

        def unreaped_members() -> tuple[tuple[int, str], ...]:
            remaining_cleanup()
            members = self.group_members(child.pid, deadline=cleanup_deadline)
            remaining_cleanup()
            require(
                members
                and len({pid for pid, _state in members}) == len(members)
                and any(pid == child.pid for pid, _state in members),
                "build_group_readback",
            )
            return members

        # Keep the leader unreaped until group termination to avoid PID reuse.
        try:
            members = unreaped_members()
        except BaseException:
            self.cleanup_failed = True
            members = None
        if self.may_signal_child and (
            members is None or len(members) != 1 or not members[0][1].startswith("Z")
        ):
            # Failed inspection must not suppress the known, unreaped child's
            # group cancellation. This signal cannot target a recycled PID.
            # At most one signal attempt per registered child. Even interrupted
            # or ambiguous wait cleanup may only reap/read, never signal again.
            self.may_signal_child = False
            try:
                remaining_cleanup()
                os.killpg(child.pid, signal.SIGKILL)
            except BaseException:
                self.cleanup_failed = True
        self.may_signal_child = False
        try:
            require(members is not None, "build_group_not_gone")
            while not (
                members is not None
                and len(members) == 1
                and members[0][1].startswith("Z")
            ):
                require(time.monotonic() < cleanup_deadline, "build_group_readback")
                members = unreaped_members()
                if len(members) != 1 or not members[0][1].startswith("Z"):
                    time.sleep(min(0.01, max(0, cleanup_deadline - time.monotonic())))
        except BaseException:
            # Preserve ownership until an unambiguous lone zombie is observed.
            # A retry may inspect/reap within the ORIGINAL bound, never signal.
            self.cleanup_failed = True
            raise BuildProcessRefused("build_group_not_gone") from None
        try:
            child.wait(timeout=remaining_cleanup())
            # A later cleanup call MUST NOT signal this now-reaped PID again.
            self.child = None
        except BaseException:
            self.cleanup_failed = True
            if child.returncode is not None:
                self.child = None
            raise BuildProcessRefused("build_child_not_reaped") from None
        finally:
            if self.child is None and child.stdout is not None:
                child.stdout.close()
        try:
            members = self.group_members(child.pid, deadline=cleanup_deadline)
            remaining_cleanup()
            if members:
                self.cleanup_failed = True
        except BaseException:
            self.cleanup_failed = True
        require(not self.cleanup_failed, "build_group_not_gone")

    def run(
        self,
        argv: list[str],
        environment: dict[str, str],
        max_bytes: int,
        sink: Callable[[bytes], object],
        *,
        cwd: Path,
        merge_stderr: bool = False,
    ) -> int:
        require(self.child is None, "build_child_already_running")
        require(not self.cleanup_failed, "build_group_not_gone")
        require(
            resource.getrlimit(resource.RLIMIT_CORE) == (0, 0),
            "core_dumps_not_disabled",
        )
        require(type(max_bytes) is int and 0 <= max_bytes <= MAX_BYTES, "output_limit")
        self.revalidate_runtime()
        self.remaining()
        try:
            # A deferred deadline/Ctrl-C is delivered only after the exact
            # Popen handle is registered inside this try/finally.
            with defer_control_signals():
                self.cleanup_deadline = None
                self.child = subprocess.Popen(
                    argv,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
                    cwd=cwd,
                    start_new_session=True,
                    close_fds=True,
                    pass_fds=(),
                )
                self.may_signal_child = True
            child = self.child
            require(child.stdout is not None, "child_output_pipe")
            if child.stdout is None:
                raise BuildProcessRefused("child_output_pipe")
            os.set_blocking(child.stdout.fileno(), False)
            size = 0
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    ready = selector.select(min(0.25, self.remaining()))
                    if not ready:
                        continue
                    chunk = os.read(child.stdout.fileno(), CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    require(size <= max_bytes, "child_output_limit")
                    sink(chunk)
            # Observe exit without reaping. Keep this exact PID allocated until
            # cancellation has killed any remaining members of its process group.
            while True:
                status = os.waitid(
                    os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
                )
                if status is not None:
                    require(
                        status.si_pid == child.pid
                        and status.si_code == os.CLD_EXITED
                        and status.si_status == 0,
                        "build_command_failed",
                    )
                    break
                time.sleep(min(0.01, self.remaining()))
            self.remaining()
        finally:
            # Even a successful leader cannot leave a hidden descendant behind.
            self.cancel_and_reap()
        self.remaining()
        return size
