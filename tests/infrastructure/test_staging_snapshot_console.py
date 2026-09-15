"""Invented Terminal/clock answers only; no actual human TTY or live consumer.

Process sealing, signal timer, descriptor metadata and terminal driver are mocked.
These tests prove interaction/control flow, not actual Terminal/native evidence.
No credentials, Azure, PostgreSQL, image, mutation, claim or receipt is used.
"""

import importlib.util
import math
import os
import stat
import sys
import termios
import unittest
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

for module_name in (
    "staging_snapshot_capture",
    "staging_snapshot_pg_io",
    "staging_snapshot_console",
):
    if module_name in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[2] / "scripts" / (module_name + ".py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
console = sys.modules["staging_snapshot_console"]
core = sys.modules["staging_snapshot_capture"]


def inputs():
    now = datetime.now(UTC)
    return core.CaptureInputs(
        core.Rule("cosmaneura-snapshot-0123456789abcdef", "8.8.8.8", "8.8.8.8"),
        "a" * 40,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        now - timedelta(minutes=5),
        now + timedelta(hours=3, minutes=55),
        now - timedelta(seconds=1),
        now + timedelta(minutes=10, seconds=-1),
    )


class ConsoleTests(unittest.TestCase):
    def start_patch(self, value):
        result = value.start()
        self.addCleanup(value.stop)
        return result

    def setUp(self):
        self.scope = inputs()
        self.now = 100.0
        self.answers = []
        self.output = bytearray()
        self.closed = []
        self.flushes = []
        self.deadlines = []
        self.bad_fds = set()
        self.groups = {}
        self.metadata = {}
        self.attributes = [0, 0, 0, termios.ICANON | termios.ECHO, 0, 0, []]
        self.start_patch(patch.object(console, "require_capture_process"))
        self.start_patch(patch.object(console, "defer_control_signals", nullcontext))
        self.start_patch(patch.object(console, "capture_deadline", self.timer))
        self.start_patch(patch.object(console.time, "monotonic", lambda: self.now))
        self.start_patch(patch.object(console.os, "dup", lambda fd: fd + 100))
        self.start_patch(patch.object(console.os, "set_inheritable"))
        self.start_patch(patch.object(console.os, "close", self.closed.append))
        self.start_patch(patch.object(console.os, "getpgrp", lambda: 55))
        self.start_patch(patch.object(console.os, "geteuid", lambda: 501))
        self.start_patch(
            patch.object(console.os, "tcgetpgrp", lambda fd: self.groups.get(fd, 55))
        )
        self.start_patch(
            patch.object(console.os, "isatty", lambda fd: fd not in self.bad_fds)
        )
        self.start_patch(patch.object(console.os, "fstat", self.fstat))
        self.start_patch(patch.object(console.os, "write", self.write))
        self.start_patch(patch.object(console.os, "read", self.read))
        self.start_patch(patch.object(console.select, "select", self.select))
        self.start_patch(
            patch.object(console.termios, "tcgetattr", lambda _fd: self.attributes)
        )
        self.start_patch(
            patch.object(
                console.termios,
                "tcflush",
                lambda fd, kind: self.flushes.append((fd, kind)),
            )
        )
        self.instance = console.SnapshotConsole(self.scope)
        self.addCleanup(self.instance.close)

    @contextmanager
    def timer(self, deadline):
        self.assertTrue(math.isfinite(deadline) and deadline > self.now)
        self.deadlines.append(deadline)
        yield

    def fstat(self, fd):
        return self.metadata.get(
            fd,
            SimpleNamespace(
                st_dev=1,
                st_ino=2,
                st_rdev=3,
                st_uid=501,
                st_mode=stat.S_IFCHR | 0o620,
            ),
        )

    def write(self, fd, raw):
        self.assertEqual(fd, 101)
        self.output.extend(raw)
        return len(raw)

    def read(self, fd, maximum):
        self.assertEqual((fd, maximum), (100, 257))
        return self.answers.pop(0)

    def select(self, readable, writable, exceptional, timeout):
        self.assertEqual((readable, writable, exceptional), ([100], [], []))
        self.assertGreater(timeout, 0)
        # Answers model later operator input, not data already queued at flush.
        return (readable if self.answers else []), [], []

    def confirm(self):
        self.answers = [b"SNAPSHOT ONLY\n", b"I WILL REMOVE IT\n"]
        self.instance.confirm_start(120)

    def present(self):
        self.confirm()
        self.instance.present_manual_window_instructions(
            self.scope,
            datetime.now(UTC) + timedelta(minutes=29),
            15,
        )

    def test_three_standard_and_owned_fds_must_be_same_foreground_terminal(self):
        for fd in (0, 1, 2, 100, 101, 102):
            with self.subTest(fd=fd):
                self.bad_fds.add(fd)
                with self.assertRaises(console.HumanConsoleRefused):
                    self.instance._check()
                self.bad_fds.clear()
                self.groups[fd] = 56
                with self.assertRaises(console.HumanConsoleRefused):
                    self.instance._check()
                self.groups.clear()

    def test_constructor_bounds_native_checks_before_any_prompt(self):
        self.assertEqual(self.deadlines, [105.0])
        self.assertEqual(self.output, b"")

    def test_redirected_descriptor_is_rejected_and_duplicates_closed(self):
        self.bad_fds.add(1)
        with self.assertRaises(console.HumanConsoleRefused):
            console.SnapshotConsole(self.scope)
        self.assertEqual(self.closed, [102, 101, 100])

    def test_mid_duplicate_failure_releases_only_owned_descriptors(self):
        with patch.object(os, "dup", side_effect=[200, OSError("fixture")]):
            with self.assertRaises(console.HumanConsoleRefused):
                console.SnapshotConsole(self.scope)
        self.assertEqual(self.closed, [200])

    def test_changed_terminal_owner_type_or_identity_refuses(self):
        for change in (
            {"st_uid": 502},
            {"st_rdev": 4},
            {"st_ino": 99},
            {"st_mode": stat.S_IFREG | 0o600},
        ):
            with self.subTest(change=change):
                value = vars(self.fstat(100)) | change
                self.metadata[100] = SimpleNamespace(**value)
                with self.assertRaises(console.HumanConsoleRefused):
                    self.instance._check()
                self.metadata.clear()

    def test_raw_or_no_echo_mode_refuses_before_prompt(self):
        for mode in (0, termios.ICANON, termios.ECHO):
            self.attributes[3] = mode
            with self.assertRaises(console.HumanConsoleRefused):
                self.instance._check()
        self.assertEqual(self.output, b"")

    def test_two_distinct_confirmations_share_deadline_and_show_exact_hashes(self):
        self.confirm()
        self.assertTrue(self.instance._confirmed)
        self.assertEqual(self.deadlines, [105.0, 220.0])
        self.assertEqual(self.flushes, [(100, termios.TCIFLUSH)] * 2)
        self.assertIn(self.scope.plan_sha256.encode(), self.output)
        self.assertIn(self.scope.identity_evidence_sha256.encode(), self.output)
        self.assertIn(self.scope.workspace_manifest_sha256.encode(), self.output)
        self.assertIn(b"Do not create any firewall rule yet", self.output)
        with self.assertRaises(console.HumanConsoleRefused):
            self.instance.confirm_start()

    def test_declined_first_confirmation_does_not_ask_second(self):
        self.answers = [b"NO\n"]
        with self.assertRaises(console.HumanConsoleRefused):
            self.instance.confirm_start()
        self.assertFalse(self.instance._confirmed)
        self.assertNotIn(b"Type I WILL REMOVE IT", self.output)

    def test_complete_removal_fallback_precedes_any_creation_instruction(self):
        self.present()
        text = self.output.decode()
        self.assertLess(text.index("REMOVE BY"), text.index("YOU may create"))
        self.assertLess(text.index("REMOVE ONLY"), text.index("YOU may create"))
        self.assertLess(
            text.index("uncertain or pending"), text.index("YOU may create")
        )
        self.assertIn(self.scope.rule.name, text)
        self.assertIn("Start IPv4: 8.8.8.8\nEnd IPv4: 8.8.8.8", text)
        self.assertIn("Azure does not auto-expire", text)

    def test_partial_fallback_never_releases_creation_and_removal_still_works(self):
        self.confirm()
        partial = True

        def partial_write(fd, raw):
            nonlocal partial
            if partial:
                partial = False
                return self.write(fd, raw[:20])
            raise OSError("fixture")

        with patch.object(os, "write", partial_write):
            with self.assertRaises(OSError):
                self.instance.present_manual_window_instructions(
                    self.scope,
                    datetime.now(UTC) + timedelta(minutes=29),
                    15,
                )
        self.assertNotIn(b"YOU may create", self.output)
        self.assertIn(b"REMOVE BY", self.output)
        self.answers = [b"ALL FINISHED AND REMOVED\n"]
        result = self.instance.await_human_rule_removal(self.scope.rule, 15)
        self.assertEqual(result, core.HumanClosure(True, True))

    def test_presentation_cannot_repeat_or_change_rule_scope(self):
        self.present()
        for scope in (self.scope, replace(self.scope, plan_sha256="f" * 64)):
            with self.assertRaises(console.HumanConsoleRefused):
                self.instance.present_manual_window_instructions(
                    scope,
                    datetime.now(UTC) + timedelta(minutes=29),
                    15,
                )

    def test_creation_requires_instructions_and_one_exact_answer(self):
        with self.assertRaises(console.HumanConsoleRefused):
            self.instance.await_human_rule_creation(self.scope.rule, 30)
        self.present()
        self.answers = [b"CREATED\n"]
        self.assertTrue(self.instance.await_human_rule_creation(self.scope.rule, 30))
        with self.assertRaises(console.HumanConsoleRefused):
            self.instance.await_human_rule_creation(self.scope.rule, 30)

    def test_wrong_rule_or_declined_creation_is_not_success(self):
        self.present()
        with self.assertRaises(console.HumanConsoleRefused):
            self.instance.await_human_rule_creation(
                replace(self.scope.rule, end="8.8.4.4"), 30
            )
        self.answers = [b"CANCEL\n"]
        self.assertFalse(self.instance.await_human_rule_creation(self.scope.rule, 30))

    def test_removal_does_not_require_valid_artifacts_or_completed_instructions(self):
        self.instance._inputs = replace(
            self.scope,
            plan_expires_at=datetime.now(UTC) - timedelta(hours=1),
            handoff_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        self.answers = [b"ALL FINISHED AND REMOVED\n"]
        result = self.instance.await_human_rule_removal(self.scope.rule, 240)
        self.assertEqual(result, core.HumanClosure(True, True))
        self.assertIn(b"HUMAN REMOVAL REQUIRED NOW", self.output)
        self.assertNotIn(b"YOU may create", self.output)

    def test_absence_or_pending_mutation_answer_never_attests_closure(self):
        self.answers = [b"RULE ABSENT BUT CREATE PENDING\n"]
        result = self.instance.await_human_rule_removal(self.scope.rule, 30)
        self.assertEqual(result, core.HumanClosure(False, False))
        with self.assertRaises(console.HumanConsoleRefused):
            self.instance.await_human_rule_removal(self.scope.rule, 30)

    def test_timeout_and_noncanonical_or_oversize_answers_refuse(self):
        for answer in (
            None,
            b"",
            b"CREATED",
            b"CREATED\nCREATED\n",
            b"\x1bCREATED\n",
            b"x" * 256 + b"\n",
        ):
            with self.subTest(answer=answer):
                self.answers = [] if answer is None else [answer]
                with self.assertRaises(console.HumanConsoleRefused):
                    self.instance._ask("Fixture: ", b"CREATED", 130)

    def test_late_answer_cannot_extend_absolute_deadline(self):
        self.answers = [b"CREATED\n"]

        def late_read(fd, maximum):
            self.now = 131
            return self.read(fd, maximum)

        with patch.object(os, "read", late_read):
            with self.assertRaises(console.HumanConsoleRefused):
                self.instance._ask("Fixture: ", b"CREATED", 130)

    def test_terminal_replacement_during_answer_is_not_accepted(self):
        self.answers = [b"CREATED\n"]

        def changed_read(fd, maximum):
            self.bad_fds.add(0)
            return self.read(fd, maximum)

        with patch.object(os, "read", changed_read):
            with self.assertRaises(console.HumanConsoleRefused):
                self.instance._ask("Fixture: ", b"CREATED", 130)

    def test_deadline_inputs_fail_closed_and_close_is_idempotent(self):
        for value in (False, 0, -1, float("nan"), float("inf"), 241):
            with self.subTest(value=value):
                with self.assertRaises(console.HumanConsoleRefused):
                    self.instance._deadline(value, 240)
        self.instance.close()
        self.instance.close()
        self.assertEqual(self.closed, [102, 101, 100])
        with self.assertRaises(console.HumanConsoleRefused):
            self.instance._check()


if __name__ == "__main__":
    unittest.main()
