"""Foreground Terminal interaction for the reviewed HUMAN snapshot launcher.

No CLI, resource mutation, cache access, secret, network, file creation, claim,
plan acceptance or executable entrypoint. Source/plan/handoff/identity gates
belong to the protected launcher. A TTY and typed answers alone confer none of
that authority. Never drive this against a real Terminal from an agent.
"""

from __future__ import annotations

import math
import os
import select
import stat
import termios
import time
from datetime import UTC, datetime, timedelta

from staging_snapshot_capture import (
    MAX_CREATION_SECONDS,
    MAX_REMOVAL_SECONDS,
    CaptureInputs,
    HumanClosure,
    Rule,
)
from staging_snapshot_pg_io import (
    capture_deadline,
    defer_control_signals,
    require_capture_process,
)


class HumanConsoleRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise HumanConsoleRefused("snapshot_human_console_refused")


def identity(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_rdev, value.st_uid, value.st_mode


class ForegroundConsole:
    """Only owned foreground public Terminal I/O, no capture authority."""

    __slots__ = ("_fds", "_identities")

    def __init__(self):
        require_capture_process()
        self._fds: list[int] = []
        self._identities: tuple[tuple[int, ...], ...] = ()
        try:
            with capture_deadline(time.monotonic() + 5):
                for descriptor in (0, 1, 2):
                    with defer_control_signals():
                        self._fds.append(os.dup(descriptor))
                    os.set_inheritable(self._fds[-1], False)
                self._identities = tuple(identity(os.fstat(fd)) for fd in self._fds)
                self._check()
        except BaseException:
            self.close()
            raise HumanConsoleRefused("snapshot_human_console_refused") from None

    def _check(self) -> None:
        require_capture_process()
        require(len(self._fds) == len(self._identities) == 3)
        group = os.getpgrp()
        require(group > 0 and len({item[2] for item in self._identities}) == 1)
        for original, (owned, expected) in enumerate(
            zip(self._fds, self._identities, strict=True)
        ):
            for descriptor in (original, owned):
                current = os.fstat(descriptor)
                require(
                    stat.S_ISCHR(current.st_mode)
                    and current.st_uid == os.geteuid()
                    and identity(current) == expected
                    and os.isatty(descriptor)
                    and os.tcgetpgrp(descriptor) == group
                )
        # Canonical, echoed public answers only. No secret/raw-mode entry.
        attributes = termios.tcgetattr(self._fds[0])
        require(attributes[3] & termios.ICANON and attributes[3] & termios.ECHO)

    def close(self) -> None:
        failed = False
        with defer_control_signals():
            while self._fds:
                descriptor = self._fds.pop()
                try:
                    os.close(descriptor)
                except OSError:
                    failed = True
        require(not failed)

    def _deadline(self, timeout: float, maximum: int) -> float:
        require(
            type(timeout) in (int, float)
            and math.isfinite(timeout)
            and 0 < timeout <= maximum
        )
        return time.monotonic() + timeout

    def _write(self, text: str, deadline: float) -> None:
        self._check()
        raw = text.encode("ascii")
        require(0 < len(raw) <= 8192)
        view = memoryview(raw)
        while view:
            require(time.monotonic() < deadline)
            count = os.write(self._fds[1], view)
            require(count > 0)
            view = view[count:]
        self._check()

    def _ask(self, prompt: str, answer: bytes, deadline: float) -> bool:
        self._check()
        # A pasted batch or stale stdin cannot answer multiple confirmations.
        # This changes only this owned Terminal's pending input, not its mode.
        termios.tcflush(self._fds[0], termios.TCIFLUSH)
        self._write(prompt, deadline)
        remaining = deadline - time.monotonic()
        require(remaining > 0)
        readable, _, _ = select.select([self._fds[0]], [], [], remaining)
        require(readable == [self._fds[0]])
        self._check()
        raw = os.read(self._fds[0], 257)
        self._check()
        require(time.monotonic() < deadline)
        require(0 < len(raw) <= 256 and raw.endswith(b"\n"))
        require(all(32 <= value <= 126 for value in raw[:-1]))
        return raw == answer + b"\n"


class PreparationConsole(ForegroundConsole):
    """One READ-ONLY confirmation before even accessing configured credentials."""

    __slots__ = ("_answered",)

    def __init__(self):
        self._answered = False
        super().__init__()

    def confirm_preparation(self, preparation_sha256: str, timeout: float) -> None:
        require(not self._answered)
        self._answered = True
        require(
            type(preparation_sha256) is str
            and len(preparation_sha256) == 64
            and all(value in "0123456789abcdef" for value in preparation_sha256)
        )
        with capture_deadline(deadline := self._deadline(timeout, 120)):
            self._write(
                "HUMAN READ-ONLY PREPARATION. No database credentials or capture.\n"
                "No firewall changes, migrations or deployment.\n"
                f"Preparation SHA256: {preparation_sha256}\n"
                "Match the independently reviewed command. This reads your configured "
                "Azure account/token using a private copy on the encrypted workspace, "
                "observes fixed staging metadata, and checks api.ipify.org once.\n",
                deadline,
            )
            require(self._ask("Type PREPARE to continue: ", b"PREPARE", deadline))


class SnapshotConsole(ForegroundConsole):
    """Retain Terminal until AFTER capture/removal, including on expiry/failure.

    Answers alone never prove Portal completion; authenticated closure does.
    """

    __slots__ = (
        "_inputs",
        "_confirmed",
        "_instructions",
        "_creation_answered",
        "_removal_answered",
        "_closes_at",
    )

    def __init__(self, inputs: CaptureInputs):
        require_capture_process()
        require(type(inputs) is CaptureInputs)
        inputs.validate(datetime.now(UTC))
        self._inputs = inputs
        self._confirmed = self._instructions = False
        self._creation_answered = self._removal_answered = False
        self._closes_at: datetime | None = None
        super().__init__()

    def confirm_start(self, timeout: float = 120) -> None:
        require(not self._confirmed and not self._instructions)
        self._inputs.validate(datetime.now(UTC))
        deadline = min(
            self._deadline(timeout, 120),
            time.monotonic()
            + (self._inputs.handoff_expires_at - datetime.now(UTC)).total_seconds(),
        )
        with capture_deadline(deadline):
            self._write(
                "HUMAN SNAPSHOT ONLY. No migration or deployment.\n"
                f"Source: {self._inputs.source_commit}\n"
                f"Plan SHA256: {self._inputs.plan_sha256}\n"
                f"Identity evidence SHA256: {self._inputs.identity_evidence_sha256}\n"
                f"Workspace manifest SHA256: {self._inputs.workspace_manifest_sha256}\n"
                f"Plan expires: {self._inputs.plan_expires_at.isoformat()}\n"
                f"Handoff expires: {self._inputs.handoff_expires_at.isoformat()}\n"
                "These identifiers must match the independently reviewed handoff.\n"
                "Do not create any firewall rule yet.\n",
                deadline,
            )
            require(
                self._ask(
                    "Type SNAPSHOT ONLY to continue: ", b"SNAPSHOT ONLY", deadline
                )
            )
            require(
                self._ask(
                    "You remain responsible for removal within 30 minutes, even on "
                    "failure. You may perform the exact Portal steps or explicitly "
                    "delegate them to an assistant only while you actively observe "
                    "and supervise the exact creation AND removal in Azure Portal. "
                    "If you cannot supervise, cancel. Only you may enter these "
                    "Terminal confirmations. Concurrent edits are not prevented. "
                    "Type I WILL REMOVE IT to confirm: ",
                    b"I WILL REMOVE IT",
                    deadline,
                )
            )
            self._inputs.validate(datetime.now(UTC))
            self._confirmed = True

    def _target(self) -> str:
        return (
            "Azure Portal target (no helper mutation):\n"
            "Subscription: bab2a709-1fcb-4c87-a9fc-802f7477ec13\n"
            "Resource group: cosmaneura-staging\n"
            "PostgreSQL flexible server: cosmaneura-staging-postgres\n"
            "Open Networking > Firewall rules.\n"
        )

    def _removal(self) -> str:
        return (
            self._target() + f"REMOVE ONLY the rule named {self._inputs.rule.name}.\n"
            "Delegated removal is permitted only while you actively observe and "
            "supervise the exact removal in Azure Portal. Otherwise remove the "
            "rule yourself; removal remains required even on failure.\n"
            "Wait for every creation/removal attempt to finish, including any "
            "uncertain or pending operation. Verify removal after they finish.\n"
            "Preserve every pre-existing rule, including Azure-services and "
            "aca-staging-egress. If another person changed this rule, stop and "
            "resolve that edit; do not assume exclusive ownership.\n"
        )

    def present_manual_window_instructions(
        self, inputs: CaptureInputs, window_closes_at: datetime, timeout: float
    ) -> None:
        require(type(inputs) is CaptureInputs and inputs == self._inputs)
        require(self._confirmed and not self._instructions)
        inputs.validate(datetime.now(UTC))
        require(
            type(window_closes_at) is datetime
            and window_closes_at.tzinfo is UTC
            and 0 < (window_closes_at - datetime.now(UTC)).total_seconds() <= 1800
        )
        self._instructions = True
        self._closes_at = window_closes_at
        deadline = min(
            self._deadline(timeout, 30),
            time.monotonic() + (window_closes_at - datetime.now(UTC)).total_seconds(),
        )
        with capture_deadline(deadline):
            # Complete fallback/deadline output BEFORE any creation instruction.
            self._write(
                f"REMOVE BY {window_closes_at.isoformat()} (UTC), even if this "
                "process fails, expires or exits. Azure does not auto-expire "
                "this rule. Keep this fallback visible:\n" + self._removal(),
                deadline,
            )
            inputs.validate(datetime.now(UTC))
            require(datetime.now(UTC) < window_closes_at)
            self._write(
                "Only after the checks above, you or your explicitly delegated "
                "assistant, only while you actively observe and supervise the "
                "exact creation in Azure Portal, may create and save exactly:\n"
                f"Rule name: {inputs.rule.name}\n"
                f"Start IPv4: {inputs.rule.start}\nEnd IPv4: {inputs.rule.end}\n"
                "No range, other rule, resource, role, or permission change.\n",
                deadline,
            )

    def await_human_rule_creation(self, rule: Rule, timeout: float) -> bool:
        require(type(rule) is Rule and rule == self._inputs.rule)
        require(self._instructions and not self._creation_answered)
        closes_at = self._closes_at
        if closes_at is None:
            raise HumanConsoleRefused("snapshot_human_console_refused")
        require(datetime.now(UTC) < closes_at)
        require(datetime.now(UTC) < self._inputs.handoff_expires_at)
        self._creation_answered = True
        now = datetime.now(UTC)
        deadline = min(
            self._deadline(timeout, MAX_CREATION_SECONDS),
            time.monotonic()
            + (self._inputs.handoff_expires_at - now).total_seconds()
            - 210,
            time.monotonic() + (closes_at - now).total_seconds() - 690,
        )
        confirmation_until = now + timedelta(seconds=deadline - time.monotonic())
        with capture_deadline(deadline):
            accepted = self._ask(
                f"CREATED BY {confirmation_until.isoformat()} (UTC). "
                "If this expires, do not create the rule; complete removal instead.\n"
                "After the exact Portal creation operation has finished successfully, "
                "type CREATED. Any other answer cancels capture: ",
                b"CREATED",
                deadline,
            )
            require(self._closes_at == closes_at and datetime.now(UTC) < closes_at)
            require(datetime.now(UTC) < self._inputs.handoff_expires_at)
            return accepted

    def await_human_rule_removal(self, rule: Rule, timeout: float) -> HumanClosure:
        require(type(rule) is Rule and rule == self._inputs.rule)
        require(not self._removal_answered)
        self._removal_answered = True
        # Deliberately no plan/handoff expiry or capture-success prerequisite.
        with capture_deadline(deadline := self._deadline(timeout, MAX_REMOVAL_SECONDS)):
            self._write("HUMAN REMOVAL REQUIRED NOW.\n" + self._removal(), deadline)
            confirmation_until = datetime.now(UTC) + timedelta(
                seconds=deadline - time.monotonic()
            )
            accepted = self._ask(
                f"Confirm removal by {confirmation_until.isoformat()} (UTC). "
                "The original REMOVE BY deadline still applies even after this "
                "prompt expires.\n"
                "ONLY after ALL mutation attempts are terminal AND the exact "
                "rule is removed, type ALL FINISHED AND REMOVED: ",
                b"ALL FINISHED AND REMOVED",
                deadline,
            )
            return HumanClosure(accepted, accepted)


if __name__ == "__main__":
    raise SystemExit("snapshot_console_requires_reviewed_human_launcher")
