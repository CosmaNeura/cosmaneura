"""Actual core/adapter/metadata I/O with invented state; NO human/live execution.

Bootstrap, artifact authority, identity, CLI, HTTP, Terminal, encryption and dump
outcomes are explicitly mocked. Only small invented local files/descriptors,
the real alarm/process seal and native metadata ACL calls are exercised.
"""

import atexit
import functools
import hashlib
import importlib.util
import json
import locale
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

for name in (
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
):
    if name in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[2] / "scripts" / (name + ".py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
ops = sys.modules["staging_snapshot_operations"]
core = sys.modules["staging_snapshot_capture"]
pg = sys.modules["staging_snapshot_pg_io"]
FILES = None


class SourceRuntimeBinding:
    """Explicit fixture substitution for actual bootstrap/runtime attestation."""

    files: Any
    runtime: Any
    postgresql: Any

    def revalidate(self):
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0


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
class OperationsTests(unittest.TestCase):
    def setUp(self):
        if FILES is None:
            return
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="snapshot-invented-operations-")
        )
        self.root = Path(root).resolve()
        self.mount, self.payload = self.root / "mount", self.root / "mount/payload"
        self.mount.mkdir(mode=0o700)
        self.payload.mkdir(mode=0o700)
        mount_fd, payload_fd = (
            os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            for path in (self.mount, self.payload)
        )
        self.stack.callback(os.close, mount_fd)
        self.stack.callback(os.close, payload_fd)
        self.events, self.identity_lifetimes = [], []
        self.fail_at = None
        self.accept_creation = self.accept_removal = True
        self.rule_changed = False
        self.artifacts_fail = False
        now = datetime.now(UTC)
        self.identity: dict[str, object] = {
            key: str(index + 1) * 64 for index, key in enumerate(ops.IDENTITY_KEYS)
        }
        self.identity.update(
            {
                "configured_home_account_linked_to_exact_arm_token": True,
                "authenticated_same_token_arm_read": True,
                "authenticated_same_token_read_required": False,
                "human_apply_authorized": False,
            }
        )
        self.inputs = core.CaptureInputs(
            core.Rule("cosmaneura-snapshot-0123456789abcdef", "8.8.8.8", "8.8.8.8"),
            "a" * 40,
            "b" * 64,
            hashlib.sha256(ops.canonical(self.identity)).hexdigest(),
            "d" * 64,
            "e" * 64,
            now - timedelta(minutes=10),
            now + timedelta(hours=3, minutes=50),
            now - timedelta(seconds=10),
            now + timedelta(seconds=590),
        )
        self.source = SourceRuntimeBinding()
        self.source.files = FILES
        self.source.runtime = object.__new__(ops.SnapshotRuntime)
        self.source.runtime._files = FILES
        self.source.postgresql = object.__new__(ops.PostgreSQLRuntime)
        self.source.postgresql._files = FILES
        workspace = object.__new__(ops.SnapshotWorkspace)
        workspace._files, workspace._runtime = FILES, self.source.runtime
        workspace._manifest_sha = self.inputs.workspace_manifest_sha256
        mounted, payload = os.fstat(mount_fd), os.fstat(payload_fd)
        self.area = ops.BoundArea(
            workspace,
            self.mount,
            mount_fd,
            payload_fd,
            mounted.st_dev,
            mounted.st_ino,
            payload.st_ino,
        )
        configured = object.__new__(ops.ConfiguredSource)
        configured._files = FILES
        configuration = ops.BoundConfiguration(
            configured, self.area, self.mount / "invented-config", -1, -1, 1, 2
        )
        cli = object.__new__(ops.OwnedAzureCLI)
        cli._runtime, cli._copy = self.source.runtime, configuration
        self.session = object.__new__(ops.SnapshotSession)
        self.session._cli, self.session._files = cli, FILES
        self.session._token = "invented-token-" * 16
        self.console = object.__new__(ops.SnapshotConsole)
        self.console._inputs, self.console._confirmed = self.inputs, True
        self.console._instructions = self.console._creation_answered = False
        self.console._removal_answered = False
        self.console._closes_at = None
        self.console._fds, self.console._identities = [], ()
        self.baseline = (core.Rule("Existing", "0.0.0.0", "0.0.0.0"),)
        self.current_rules = self.baseline
        self.application_sha = "f" * 64
        self.dump_bytes = b"PGDMP invented dump fixture"
        owner = self

        def verify_session(_self, *, deadline, minimum_remaining_seconds=240):
            owner.outside_alarm("identity")
            owner.assertGreater(deadline, time.monotonic())
            owner.identity_lifetimes.append(minimum_remaining_seconds)
            return dict(owner.identity)

        def observe_server(_self, timeout):
            owner.outside_alarm("server")
            owner.assertGreater(timeout, 0)
            return {"target_matches": True, "identity_evidence": dict(owner.identity)}

        def rules(azure, timeout):
            owner.outside_alarm("rules")
            azure._identity = dict(owner.identity)
            return owner.current_rules

        def credentials(_self, image, timeout):
            owner.outside_alarm("credentials")
            owner.assertEqual(image, ops.IMAGE)
            return pg.Credentials("invented", "fixture-only"), owner.application_sha

        def workspace_check(_self, area, *, deadline):
            owner.inside_alarm("workspace")
            owner.assertIs(area, owner.area)
            owner.assertGreater(deadline, time.monotonic())
            return {}

        def ip(_self, timeout):
            owner.outside_alarm("ip")
            return owner.inputs.rule.start

        def instructions(console, inputs, closes_at, timeout):
            owner.outside_alarm("instructions")
            console._instructions, console._closes_at = True, closes_at

        def creation(console, rule, timeout):
            owner.outside_alarm("creation")
            console._creation_answered = True
            if owner.accept_creation:
                owner.current_rules = (*owner.baseline, rule)
            return owner.accept_creation

        def removal(console, rule, timeout):
            owner.outside_alarm("removal")
            console._removal_answered = True
            if owner.accept_removal:
                owner.current_rules = owner.baseline
            if owner.rule_changed:
                owner.current_rules = (core.Rule("different", "0.0.0.0", "0.0.0.0"),)
            return core.HumanClosure(owner.accept_removal, owner.accept_removal)

        def capture(credentials, binding, children, encrypted_check, max_bytes):
            owner.outside_alarm("capture_enter")
            owner.assertEqual(credentials, pg.Credentials("invented", "fixture-only"))
            owner.assertIs(children, owner.adapter._children)
            owner.assertEqual(binding.directory_fd, owner.area.payload_fd)
            with pg.capture_deadline(children.deadline):
                children.revalidate_runtime()
                encrypted_check()
                owner.inside_alarm("dump")
                if owner.fail_at == "dump":
                    raise RuntimeError("invented capture failure")
                with ops.private_new_file(binding.directory_fd, pg.ARCHIVE_NAME) as fd:
                    os.write(fd, owner.dump_bytes)
                    os.fchmod(fd, 0o400)
                encrypted_check()
            return pg.DumpResult(
                len(owner.dump_bytes), hashlib.sha256(owner.dump_bytes).hexdigest()
            )

        def cancel(children):
            owner.inside_alarm("cancel")
            owner.assertIs(children, owner.adapter._children)
            if owner.fail_at == "cancel":
                raise RuntimeError("invented cleanup uncertainty")

        for cls, name, function in (
            (ops.SnapshotSession, "verify", verify_session),
            (ops.SnapshotAzure, "observe_server", observe_server),
            (ops.SnapshotAzure, "list_rules", rules),
            (ops.SnapshotAzure, "read_bound_database_credentials", credentials),
            (ops.SnapshotWorkspace, "revalidate", workspace_check),
            (ops.SnapshotPublicIP, "current_ipv4", ip),
            (ops.SnapshotConsole, "present_manual_window_instructions", instructions),
            (ops.SnapshotConsole, "await_human_rule_creation", creation),
            (ops.SnapshotConsole, "await_human_rule_removal", removal),
            (ops.BoundedChildren, "cancel_and_reap", cancel),
        ):
            self.stack.enter_context(patch.object(cls, name, function))
        self.stack.enter_context(
            patch.object(ops, "capture_with_verified_payload", capture)
        )
        self.adapter = self.make_adapter()

    def inside_alarm(self, event):
        self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.events.append(event)

    def outside_alarm(self, event):
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.events.append(event)
        if self.fail_at == event:
            raise RuntimeError("invented " + event + " failure")

    def artifact_check(self):
        self.inside_alarm("artifacts")
        if self.artifacts_fail:
            raise RuntimeError("invented changed artifact")

    def make_adapter(self, **changes):
        values = dict(
            inputs=self.inputs,
            source=self.source,
            area=self.area,
            session=self.session,
            console=self.console,
            identity_evidence=self.identity,
            application_binding_sha256=self.application_sha,
            preexisting_rules=self.baseline,
            artifact_revalidate=self.artifact_check,
        )
        values.update(changes)
        return ops.SnapshotOperations(**values)

    def execute(self):
        return core.execute_after_human_gate(self.inputs, self.adapter)

    def test_complete_core_sequence_publishes_only_after_human_and_readback(self):
        metadata = self.execute()
        path = self.mount / ops.METADATA_NAME
        self.assertEqual(json.loads(path.read_bytes()), metadata)
        self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        self.assertEqual(
            metadata["archive_sha256"], hashlib.sha256(self.dump_bytes).hexdigest()
        )
        self.assertFalse(metadata["deployment_authorized"])
        self.assertFalse(metadata["restore_or_migration_accepted"])
        self.assertTrue(metadata["not_a_receipt_or_reusable_authorization"])
        self.assertLess(self.events.index("dump"), self.events.index("cancel"))
        self.assertLess(self.events.index("cancel"), self.events.index("removal"))
        self.assertEqual(self.events.count("ip"), 2)
        self.assertEqual(self.events.count("credentials"), 1)
        self.assertEqual(self.events.count("rules"), 3)
        self.assertTrue(self.adapter._closure_verified)
        self.assertTrue(any(value >= 1825 for value in self.identity_lifetimes))
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_linkage_graph_and_real_confirmation_are_required(self):
        self.console._confirmed = False
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.make_adapter()
        self.console._confirmed = True
        self.session._cli._runtime = object.__new__(ops.SnapshotRuntime)
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.make_adapter()
        self.assertEqual(self.events, [])

    def test_live_baseline_must_match_the_reviewed_plan_before_instructions(self):
        self.current_rules = (core.Rule("Unreviewed", "0.0.0.0", "0.0.0.0"),)
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertNotIn("instructions", self.events)
        self.assertNotIn("credentials", self.events)

    def test_planned_new_rule_cannot_already_exist_in_reviewed_baseline(self):
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.make_adapter(preexisting_rules=(self.inputs.rule,))

    def test_identity_digest_and_authenticated_linkage_required(self):
        changed = {**self.identity, "exact_arm_token_sha256": "9" * 64}
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.make_adapter(identity_evidence=changed)
        self.identity["authenticated_same_token_arm_read"] = False
        inputs = replace(
            self.inputs,
            identity_evidence_sha256=hashlib.sha256(
                ops.canonical(self.identity)
            ).hexdigest(),
        )
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.make_adapter(inputs=inputs)

    def test_identity_change_refuses_before_instructions(self):
        self.identity["current_token_oid_sha256"] = "9" * 64
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertNotIn("instructions", self.events)
        self.assertNotIn("credentials", self.events)

    def test_changed_artifact_refuses_before_network_or_human_instructions(self):
        self.artifacts_fail = True
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertNotIn("identity", self.events)
        self.assertNotIn("instructions", self.events)

    def test_nonempty_payload_refuses_without_touching_existing_file(self):
        existing = self.payload / "prior"
        existing.write_bytes(b"preserved")
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertEqual(existing.read_bytes(), b"preserved")
        self.assertNotIn("instructions", self.events)

    def test_partial_instructions_still_request_removal(self):
        self.fail_at = "instructions"
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertIn("removal", self.events)
        self.assertNotIn("creation", self.events)
        self.assertFalse((self.mount / ops.METADATA_NAME).exists())

    def test_declined_creation_still_requests_removal_without_credentials(self):
        self.accept_creation = False
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertIn("removal", self.events)
        self.assertNotIn("credentials", self.events)

    def test_capture_failure_reuses_owned_cleanup_handle_and_removes(self):
        self.fail_at = "dump"
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertIsNotNone(self.adapter._children)
        self.assertEqual(self.events.count("cancel"), 1)
        self.assertIn("removal", self.events)
        self.assertFalse((self.mount / ops.METADATA_NAME).exists())

    def test_cleanup_failure_does_not_suppress_human_removal(self):
        self.fail_at = "cancel"
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertIn("removal", self.events)
        self.assertFalse((self.mount / ops.METADATA_NAME).exists())

    def test_unknown_human_removal_never_publishes(self):
        self.accept_removal = False
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertFalse(self.adapter._closure_verified)
        self.assertFalse((self.mount / ops.METADATA_NAME).exists())

    def test_changed_preexisting_rule_never_publishes(self):
        self.rule_changed = True
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertFalse(self.adapter._closure_verified)
        self.assertFalse((self.mount / ops.METADATA_NAME).exists())

    def test_expired_artifacts_cannot_suppress_removal_fallback(self):
        self.adapter._inputs = replace(
            self.inputs, plan_expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        self.artifacts_fail = True
        closure = self.adapter.await_human_rule_removal(self.inputs.rule, 2)
        self.assertEqual(closure, core.HumanClosure(True, True))
        self.assertEqual(self.events, ["removal"])

    def test_expired_capture_and_artifacts_cannot_suppress_owned_child_cleanup(self):
        children = ops.BoundedChildren(time.monotonic() + 1, self.artifact_check)
        children.deadline = time.monotonic() - 1
        self.adapter._children = children
        self.adapter._inputs = replace(
            self.inputs, plan_expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        self.artifacts_fail = True
        self.adapter.cancel_capture_and_reap(2)
        self.assertEqual(self.events, ["cancel"])
        self.assertIs(self.adapter._children, children)

    def test_application_binding_drift_refuses_dump_but_removes(self):
        self.application_sha = "9" * 64
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertIn("removal", self.events)
        self.assertNotIn("dump", self.events)

    def test_foreign_inputs_and_fake_metadata_refused(self):
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.adapter.revalidate_human_gate(
                replace(self.inputs, plan_sha256="9" * 64), 2
            )
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.adapter.publish_metadata({"deployment_authorized": True}, 2)
        self.assertEqual(self.events, [])

    def test_existing_metadata_is_preserved_and_success_refused(self):
        path = self.mount / ops.METADATA_NAME
        path.write_bytes(b"preserve existing result")
        with self.assertRaises(core.CaptureRefused):
            self.execute()
        self.assertEqual(path.read_bytes(), b"preserve existing result")
        self.assertTrue(self.adapter._closure_verified)

    def test_changed_metadata_cannot_relabel_captured_archive_after_closure(self):
        publish = ops.SnapshotOperations.publish_metadata

        def changed_metadata(adapter, metadata, timeout):
            publish(adapter, {**metadata, "archive_sha256": "9" * 64}, timeout)

        with patch.object(ops.SnapshotOperations, "publish_metadata", changed_metadata):
            with self.assertRaises(core.CaptureRefused):
                self.execute()
        self.assertTrue(self.adapter._closure_verified)
        self.assertTrue((self.payload / pg.ARCHIVE_NAME).exists())
        self.assertFalse((self.mount / ops.METADATA_NAME).exists())

    def test_publication_failure_after_closure_keeps_capture_not_success(self):
        with patch.object(
            ops.os, "fsync", side_effect=OSError("invented storage failure")
        ):
            with self.assertRaises(core.CaptureRefused):
                self.execute()
        self.assertTrue(self.adapter._closure_verified)
        self.assertTrue((self.mount / ops.METADATA_NAME).exists())
        self.assertTrue((self.payload / pg.ARCHIVE_NAME).exists())

    def test_second_capture_and_second_publication_refused(self):
        metadata = self.execute()
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.adapter.capture_to_exclusive_encrypted_descriptor(self.inputs, 2, 1024)
        with self.assertRaises(ops.SnapshotOperationsRefused):
            self.adapter.publish_metadata(metadata, 2)
        self.assertEqual(self.events.count("credentials"), 1)


if __name__ == "__main__":
    assert callable(atexit.register)
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    FILES = ops.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
