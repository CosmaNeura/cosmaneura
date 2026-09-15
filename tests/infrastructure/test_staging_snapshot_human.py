"""Invented orchestration fixtures ONLY; no real human consumer is executed.

Every artifact, source authority, workspace, account/cache, CLI, ARM response,
Terminal confirmation and capture operation is explicitly replaced. Only the
entry orchestration and native process/alarm guards are real. No network or
credential reads, native image, database, firewall mutation or claim is used.
"""

import atexit
import functools
import importlib.util
import locale
import signal
import subprocess
import sys
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

NAMES = (
    "staging_encrypted_workspace",
    "staging_snapshot_capture",
    "staging_snapshot_identity",
    "staging_snapshot_pg_io",
    "staging_snapshot_native_files",
    "staging_snapshot_runtime",
    "staging_snapshot_postgresql_runtime",
    "staging_snapshot_workspace",
    "staging_snapshot_configuration",
    "staging_snapshot_cli",
    "staging_snapshot_session",
    "staging_snapshot_azure",
    "staging_snapshot_console",
    "staging_snapshot_public_ip",
    "staging_snapshot_operations",
    "staging_snapshot_artifacts",
    "staging_snapshot_human",
)
for name in NAMES:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parents[2] / "scripts" / (name + ".py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
human = sys.modules["staging_snapshot_human"]
pg = sys.modules["staging_snapshot_pg_io"]
native = sys.modules["staging_snapshot_native_files"]
FILES = None


class SourceRuntimeBinding:
    def __init__(self, owner):
        self.owner = owner
        self.files = FILES
        self.runtime = object()

    def revalidate(self):
        self.owner.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.owner.step("source")


def isolated_tests(cls):
    for name, method in list(vars(cls).items()):
        if not name.startswith("test_"):
            continue

        @functools.wraps(method)
        def run(self, _method=method, _name=name):
            if FILES is not None:
                return _method(self)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    __file__,
                    cls.__name__ + "." + _name,
                ],
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


@isolated_tests
class HumanEntryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(human, "_attempted", []))
        self.stack.enter_context(
            patch.object(
                sys.modules["__main__"],
                "SourceRuntimeBinding",
                SourceRuntimeBinding,
                create=True,
            )
        )
        self.events = []
        self.fail_at = set()
        self.advance_at = {}
        self.clock = time.monotonic()
        self.started = self.clock
        self.stack.enter_context(
            patch.object(human, "time", SimpleNamespace(monotonic=lambda: self.clock))
        )
        self.source: Any = SourceRuntimeBinding(self)
        self.packet = Path("/private/tmp/invented-human-packet")
        self.handoff, self.review = "a" * 64, "b" * 64
        self.identity: dict[str, Any] = {
            key: str(index + 1) * 64 for index, key in enumerate(human.IDENTITY_KEYS)
        }
        self.identity.update(
            {
                "token_expires_epoch": 2000000000,
                "configured_home_account_linked_to_exact_arm_token": True,
                "authenticated_same_token_arm_read": True,
                "authenticated_same_token_read_required": False,
                "human_apply_authorized": False,
                "arm_read_body_sha256": "c" * 64,
            }
        )
        self.observed = {
            "target_matches": True,
            "identity_evidence": dict(self.identity),
            "server_body_sha256": "c" * 64,
        }
        self.plan = {
            "workspace_source_sha256": "d" * 64,
            "hdiutil_sha256": "e" * 64,
            "diskutil_sha256": "f" * 64,
            "application_binding_sha256": "9" * 64,
        }
        self.inputs = SimpleNamespace(workspace_manifest_sha256="8" * 64)
        self.baseline = {"existing": object()}
        self.area, self.copy, self.cli, self.session = (object() for _ in range(4))
        self.metadata = {
            "status": "captured_human_closure_observed",
            "deployment_authorized": False,
        }
        self.core_error = None
        self.start_checks = 0
        self.expire_at_check = None
        owner = self

        class Artifacts:
            def __init__(_self, source, packet, handoff, review):
                owner.step("artifacts")
                owner.assertIs(source, owner.source)
                owner.assertEqual(
                    (packet, handoff, review),
                    (owner.packet, owner.handoff, owner.review),
                )
                owner.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
                _self.inputs, _self.plan, _self.identity = (
                    owner.inputs,
                    owner.plan,
                    owner.identity,
                )
                _self.workspace_root = Path("/private/tmp/invented-workspace")
                _self.baseline = owner.baseline

            def revalidate(_self):
                owner.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
                owner.step("artifact_revalidate")

            def require_start(_self):
                owner.step("start_check")
                owner.start_checks += 1
                if owner.expire_at_check == owner.start_checks:
                    raise RuntimeError("invented expired evidence")

        class Console:
            def __init__(_self, inputs):
                owner.step("console")
                owner.assertIs(inputs, owner.inputs)
                _self.confirmed = False

            def confirm_start(_self, timeout):
                owner.assertGreater(timeout, 0)
                owner.assertLessEqual(timeout, 120)
                owner.step("confirm")
                _self.confirmed = True

            def close(_self):
                owner.step("console_close")

        class Workspace:
            def __init__(
                _self, files, runtime, root, manifest, source, hdiutil, diskutil
            ):
                owner.step("workspace")
                owner.assertIs(files, owner.source.files)
                owner.assertIs(runtime, owner.source.runtime)
                owner.assertEqual(
                    (manifest, source, hdiutil, diskutil),
                    ("8" * 64, "d" * 64, "e" * 64, "f" * 64),
                )

            @contextmanager
            def open_area(_self, *, deadline):
                owner.check_deadline(deadline, 30)
                owner.step("area")
                try:
                    yield owner.area
                finally:
                    owner.step("area_close")

        class Configured:
            def __init__(_self, files, *, deadline):
                owner.check_deadline(deadline, 30)
                owner.assertIs(files, owner.source.files)
                owner.step("configured")

            @contextmanager
            def private_copy(_self, area, *, deadline):
                owner.assertIs(area, owner.area)
                owner.check_deadline(deadline, 60)
                owner.step("copy")
                try:
                    yield owner.copy
                finally:
                    owner.step("copy_close")

        def cli(runtime, copied):
            owner.step("cli")
            owner.assertIs(runtime, owner.source.runtime)
            owner.assertIs(copied, owner.copy)
            return owner.cli

        def session(cli, files, *, deadline):
            owner.step("session")
            owner.check_deadline(deadline, 120)
            owner.assertIs(cli, owner.cli)
            owner.assertIs(files, owner.source.files)
            return owner.session

        class Azure:
            def __init__(_self, session):
                owner.assertIs(session, owner.session)

            def observe_server(_self, timeout):
                owner.step("identity")
                owner.assertGreater(timeout, 0)
                owner.assertLessEqual(timeout, 30)
                return owner.observed

        def operations(
            inputs,
            source,
            area,
            session,
            console,
            identity,
            application,
            baseline,
            callback,
        ):
            owner.step("operations")
            owner.assertTrue(console.confirmed)
            owner.assertIs(inputs, owner.inputs)
            owner.assertIs(source, owner.source)
            owner.assertIs(area, owner.area)
            owner.assertIs(session, owner.session)
            owner.assertIs(identity, owner.identity)
            owner.assertEqual(application, owner.plan["application_binding_sha256"])
            owner.assertEqual(baseline, tuple(owner.baseline.values()))
            owner.assertIsInstance(callback.__self__, Artifacts)
            owner.assertEqual(callback.__name__, "revalidate")
            return owner.operations

        self.operations = object()

        def capture(inputs, operations):
            owner.step("capture")
            owner.assertIs(inputs, owner.inputs)
            owner.assertIs(operations, owner.operations)
            owner.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)
            if owner.core_error is not None:
                raise owner.core_error
            return owner.metadata

        for name, value in {
            "ReviewedArtifacts": Artifacts,
            "SnapshotConsole": Console,
            "SnapshotWorkspace": Workspace,
            "ConfiguredSource": Configured,
            "OwnedAzureCLI": cli,
            "SnapshotSession": session,
            "SnapshotAzure": Azure,
            "SnapshotOperations": operations,
            "execute_after_human_gate": capture,
        }.items():
            self.stack.enter_context(patch.object(human, name, value))

    def step(self, name):
        self.events.append(name)
        self.clock += self.advance_at.get(name, 0)
        if name in self.fail_at:
            raise RuntimeError("INVENTED_SECRET_CANARY_" + name)

    def check_deadline(self, deadline, maximum):
        self.assertGreater(deadline, self.clock)
        self.assertLessEqual(deadline, self.clock + maximum)
        self.assertLessEqual(deadline, self.started + 240)

    def run_fixture(self):
        return human.run_human_snapshot(
            self.source, self.packet, self.handoff, self.review
        )

    def rejected(self, expected="snapshot_preparation_refused"):
        with self.assertRaises(human.SnapshotHumanRefused) as caught:
            self.run_fixture()
        self.assertEqual(str(caught.exception), expected)
        self.assertNotIn("INVENTED_SECRET_CANARY", str(caught.exception))

    def test_complete_mocked_sequence_preserves_order_and_closes_after_capture(self):
        self.assertIs(self.run_fixture(), self.metadata)
        for earlier, later in (
            ("artifacts", "console"),
            ("console", "configured"),
            ("identity", "confirm"),
            ("confirm", "capture"),
            ("capture", "copy_close"),
            ("copy_close", "area_close"),
            ("area_close", "console_close"),
        ):
            self.assertLess(self.events.index(earlier), self.events.index(later))
        self.assertEqual(self.events.count("capture"), 1)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_unbound_source_fails_before_artifact_or_console(self):
        self.source = object()
        self.rejected()
        self.assertEqual(self.events, [])

    def test_artifact_failure_precedes_terminal_and_account_access(self):
        self.fail_at.add("artifacts")
        self.rejected()
        self.assertNotIn("console", self.events)
        self.assertNotIn("configured", self.events)

    def test_missing_terminal_refuses_before_cache_copy(self):
        self.fail_at.add("console")
        self.rejected()
        self.assertNotIn("configured", self.events)

    def test_workspace_failure_closes_terminal_without_account_access(self):
        self.fail_at.add("area")
        self.rejected()
        self.assertNotIn("configured", self.events)
        self.assertEqual(self.events[-1], "console_close")

    def test_copy_failure_precedes_cli_and_preserves_closure(self):
        self.fail_at.add("copy")
        self.rejected()
        self.assertNotIn("cli", self.events)
        self.assertEqual(self.events[-2:], ["area_close", "console_close"])

    def test_changed_exact_token_refuses_before_confirmation(self):
        self.observed["identity_evidence"]["exact_arm_token_sha256"] = "0" * 64
        self.rejected()
        self.assertNotIn("confirm", self.events)
        self.assertNotIn("capture", self.events)

    def test_changed_server_readback_refuses_before_confirmation(self):
        self.observed["server_body_sha256"] = "0" * 64
        self.rejected()
        self.assertNotIn("confirm", self.events)

    def test_unmatched_target_refuses_before_confirmation(self):
        self.observed["target_matches"] = False
        self.rejected()
        self.assertNotIn("confirm", self.events)

    def test_declined_confirmation_never_constructs_capture_operations(self):
        self.fail_at.add("confirm")
        self.rejected()
        self.assertNotIn("operations", self.events)
        self.assertNotIn("capture", self.events)

    def test_expiry_after_confirmation_refuses_capture(self):
        self.expire_at_check = 5
        self.rejected()
        self.assertIn("confirm", self.events)
        self.assertNotIn("capture", self.events)

    def test_preparation_deadline_is_not_renewed_after_slow_stage(self):
        self.advance_at["identity"] = 241
        self.rejected()
        self.assertNotIn("confirm", self.events)

    def test_cleanup_required_code_survives_descriptor_and_terminal_failures(self):
        self.core_error = human.CaptureRefused("cleanup_failed_human_action_required")
        self.fail_at.update({"copy_close", "area_close", "console_close"})
        self.rejected("cleanup_failed_human_action_required")
        self.assertEqual(self.events[-1], "console_close")

    def test_capture_failure_with_known_closed_access_keeps_specific_code(self):
        self.core_error = human.CaptureRefused("capture_failed_access_closed")
        self.rejected("capture_failed_access_closed")

    def test_unexpected_capture_exception_is_redacted_and_requires_human_check(self):
        self.core_error = RuntimeError("INVENTED_SECRET_CANARY")
        self.rejected("snapshot_capture_failed_human_action_required")

    def test_descriptor_close_failure_after_capture_prevents_success(self):
        self.fail_at.add("copy_close")
        self.rejected("snapshot_local_close_failed_no_acceptance")

    def test_terminal_close_failure_after_capture_prevents_success(self):
        self.fail_at.add("console_close")
        self.rejected("snapshot_local_close_failed_no_acceptance")

    def test_failed_attempt_cannot_be_retried_in_same_process(self):
        self.fail_at.add("artifacts")
        self.rejected()
        before = list(self.events)
        self.fail_at.clear()
        self.rejected()
        self.assertEqual(self.events, before)


if __name__ == "__main__":
    assert callable(atexit.register)
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    FILES = native.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
