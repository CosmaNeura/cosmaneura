"""Pure in-memory snapshot sequencing tests. No cloud, database or disk I/O."""

import importlib.util
import math
import sys
import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "staging_snapshot_capture",
    Path(__file__).resolve().parents[2] / "scripts/staging_snapshot_capture.py",
)
assert SPEC is not None and SPEC.loader is not None
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)
NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


def inputs():
    return core.CaptureInputs(
        rule=core.Rule("cosmaneura-snapshot-0123456789abcdef", "8.8.8.8", "8.8.8.8"),
        source_commit="a" * 40,
        plan_sha256="b" * 64,
        identity_evidence_sha256="c" * 64,
        workspace_manifest_sha256="d" * 64,
        manual_window_approval_sha256="f" * 64,
        plan_created_at=NOW - timedelta(hours=1),
        plan_expires_at=NOW + timedelta(hours=3),
        handoff_created_at=NOW - timedelta(seconds=30),
        handoff_expires_at=NOW + timedelta(seconds=2670),
    )


class MemoryOperations:
    def __init__(self):
        self.elapsed = 0.0
        self.wall_offset = 0.0
        self.calls = []
        self.rules = {"existing": core.Rule("existing", "0.0.0.0", "0.0.0.0")}
        self.initial = dict(self.rules)
        self.failures = set()
        self.capture_seconds = 2
        self.created = False
        self.capture_hook: Callable[[], None] | None = None
        self.archive = core.Archive(2048, "e" * 64, core.SOURCE_REVISION, True, True)
        self.public_ip_calls = 0
        self.change_public_ip = False
        self.published = None
        self.unknown_create = False
        self.unknown_delete_closed = False
        self.change_preexisting = False
        self.change_new_rule = False
        self.creation_confirmed: object = True
        self.closure = core.HumanClosure(True, True)
        self.creation_seconds = 0
        self.removal_seconds = 0

    def monotonic(self):
        return 100.0 + self.elapsed

    def utcnow(self):
        return NOW + timedelta(seconds=self.elapsed + self.wall_offset)

    def step(self, name, timeout):
        assert type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
        self.calls.append((name, timeout))
        self.elapsed += 0.1
        if name in self.failures:
            raise RuntimeError("invented private diagnostic must not escape")

    def revalidate_human_gate(self, _inputs, timeout):
        self.step("human_gate", timeout)

    def revalidate_runtime_identity(self, _inputs, timeout):
        self.step("identity", timeout)

    def current_public_ipv4(self, timeout):
        self.step("public_ip", timeout)
        self.public_ip_calls += 1
        return (
            "1.1.1.1"
            if self.change_public_ip and self.public_ip_calls > 1
            else "8.8.8.8"
        )

    def list_rules(self, timeout):
        self.step("list", timeout)
        return tuple(self.rules.values())

    def verify_empty_encrypted_payload(self, _inputs, timeout):
        self.step("empty_payload", timeout)

    def present_manual_window_instructions(self, _inputs, window_closes_at, timeout):
        assert window_closes_at == NOW + timedelta(minutes=30)
        self.step("instructions", timeout)

    def await_human_rule_creation(self, rule, timeout):
        # Simulate a HUMAN in memory; no actual CLI, HTTP or database operation.
        self.step("human_create", timeout)
        self.elapsed += self.creation_seconds
        self.rules[rule.name] = rule
        self.created = True
        if self.change_preexisting:
            self.rules["existing"] = core.Rule("existing", "1.1.1.1", "1.1.1.1")
        if self.change_new_rule:
            self.rules[rule.name] = replace(rule, end="8.8.8.9")
        if self.unknown_create:
            raise TimeoutError("unknown create outcome")
        return self.creation_confirmed

    def capture_to_exclusive_encrypted_descriptor(self, _inputs, timeout, max_bytes):
        self.step("capture", timeout)
        assert max_bytes == core.MAX_ARCHIVE_BYTES
        assert timeout <= core.MAX_CAPTURE_SECONDS
        self.elapsed += self.capture_seconds
        if self.capture_hook:
            self.capture_hook()
        return self.archive

    def cancel_capture_and_reap(self, timeout):
        self.step("cancel", timeout)

    def await_human_rule_removal(self, rule, timeout):
        self.step("human_remove", timeout)
        self.elapsed += self.removal_seconds
        assert rule.name.startswith("cosmaneura-snapshot-")
        if rule.name in self.rules:
            assert self.rules[rule.name] == rule
            del self.rules[rule.name]
        if self.unknown_delete_closed:
            raise TimeoutError("unknown delete outcome")
        return self.closure

    def publish_metadata(self, metadata, timeout):
        self.step("publish", timeout)
        assert self.rules == self.initial
        self.published = metadata


class SnapshotCoreTests(unittest.TestCase):
    def test_case_insensitive_existing_name_refuses_before_create(self):
        operations = MemoryOperations()
        planned = inputs()
        existing = replace(planned.rule, name=planned.rule.name.upper())
        operations.rules[existing.name] = existing
        before = dict(operations.rules)
        with self.assertRaisesRegex(core.CaptureRefused, "planned_rule_already_exists"):
            core.execute_after_human_gate(planned, operations)
        self.assertFalse(operations.created)
        self.assertEqual(operations.rules, before)

    def test_case_insensitive_duplicate_inventory_is_rejected(self):
        with self.assertRaisesRegex(core.CaptureRefused, "duplicate_rule_names"):
            core.rules_by_name(
                (
                    core.Rule("Existing", "0.0.0.0", "0.0.0.0"),
                    core.Rule("existing", "0.0.0.0", "0.0.0.0"),
                )
            )

    def test_success_observes_human_closure_before_publication(self):
        operations = MemoryOperations()
        result = core.execute_after_human_gate(inputs(), operations)
        names = [name for name, _ in operations.calls]
        self.assertLess(names.index("human_gate"), names.index("instructions"))
        self.assertLess(names.index("identity"), names.index("instructions"))
        self.assertLess(names.index("empty_payload"), names.index("instructions"))
        self.assertLess(names.index("instructions"), names.index("human_create"))
        self.assertLess(names.index("human_remove"), names.index("publish"))
        self.assertEqual(operations.rules, operations.initial)
        self.assertTrue(result["new_rule_absent"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["restore_or_migration_accepted"])
        self.assertEqual(result["firewall_management"], "operator_supervised_portal")
        self.assertFalse(result["atomic_rule_ownership_proved"])
        self.assertFalse(result["native_firewall_expiry_proved"])
        self.assertFalse(result["crash_proof_cleanup_proved"])
        self.assertTrue(result["operator_reports_all_mutations_terminal"])
        self.assertEqual(result["manual_window_approval_sha256"], "f" * 64)
        self.assertNotIn("8.8.8.8", str(result))
        self.assertNotIn(inputs().rule.name, str(result))

    def test_invalid_or_stale_inputs_do_not_call_any_operations(self):
        candidates = [
            replace(inputs(), source_commit="master"),
            replace(inputs(), identity_evidence_sha256="not-evidence"),
            replace(inputs(), manual_window_approval_sha256="missing"),
            replace(
                inputs(),
                plan_created_at=NOW - timedelta(hours=5),
                plan_expires_at=NOW - timedelta(hours=1),
            ),
            replace(
                inputs(),
                handoff_created_at=NOW - timedelta(minutes=11),
                handoff_expires_at=NOW - timedelta(minutes=1),
            ),
            replace(inputs(), handoff_expires_at=NOW + timedelta(hours=1)),
            replace(inputs(), plan_expires_at=NOW + timedelta(hours=4)),
            replace(inputs(), handoff_created_at=NOW + timedelta(seconds=1)),
        ]
        for candidate in candidates:
            operations = MemoryOperations()
            with (
                self.subTest(candidate=candidate),
                self.assertRaises(core.CaptureRefused),
            ):
                core.execute_after_human_gate(candidate, operations)
            self.assertEqual(operations.calls, [])

    def test_private_ipv6_range_azure_wide_and_noncanonical_ip_refused(self):
        for start, end in (
            ("0.0.0.0", "0.0.0.0"),
            ("10.0.0.1", "10.0.0.1"),
            ("::1", "::1"),
            ("8.8.8.8", "8.8.8.9"),
            ("008.8.8.8", "008.8.8.8"),
            ("8.8.8.8/32", "8.8.8.8/32"),
        ):
            with (
                self.subTest(start=start, end=end),
                self.assertRaises(core.CaptureRefused),
            ):
                replace(
                    inputs(), rule=replace(inputs().rule, start=start, end=end)
                ).validate(NOW)

    def test_exact_plan_and_handoff_start_time_limits(self):
        for seconds in (119, 120):
            expires = NOW + timedelta(seconds=seconds)
            value = replace(
                inputs(),
                handoff_created_at=expires - timedelta(minutes=45),
                handoff_expires_at=expires,
            )
            with self.assertRaisesRegex(
                core.CaptureRefused, "handoff_remaining_lifetime"
            ):
                value.validate(NOW)
        for seconds in (1799, 1800):
            expires = NOW + timedelta(seconds=seconds)
            value = replace(
                inputs(),
                plan_created_at=expires - timedelta(hours=4),
                plan_expires_at=expires,
            )
            with self.assertRaisesRegex(core.CaptureRefused, "plan_remaining_lifetime"):
                value.validate(NOW)

    def test_existing_rule_is_never_overwritten_or_deleted(self):
        operations = MemoryOperations()
        operations.rules[inputs().rule.name] = inputs().rule
        with self.assertRaisesRegex(core.CaptureRefused, "planned_rule_already_exists"):
            core.execute_after_human_gate(inputs(), operations)
        self.assertNotIn("human_create", [name for name, _ in operations.calls])
        self.assertNotIn("human_remove", [name for name, _ in operations.calls])

    def test_gate_failure_before_create_has_no_mutation(self):
        for failure in ("human_gate", "identity", "public_ip", "list", "empty_payload"):
            operations = MemoryOperations()
            operations.failures.add(failure)
            with (
                self.subTest(failure=failure),
                self.assertRaisesRegex(
                    core.CaptureRefused, "snapshot_check_failed_no_acceptance"
                ) as raised,
            ):
                core.execute_after_human_gate(inputs(), operations)
            self.assertNotIn("invented private diagnostic", str(raised.exception))
            self.assertFalse(operations.created)
            self.assertNotIn("human_remove", [name for name, _ in operations.calls])

    def test_failed_human_create_with_no_rule_still_requires_closure(self):
        operations = MemoryOperations()
        operations.failures.add("human_create")
        with self.assertRaisesRegex(
            core.CaptureRefused, "capture_failed_access_closed"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertIsNone(operations.published)
        self.assertIn("cancel", [name for name, _ in operations.calls])
        self.assertIn("human_remove", [name for name, _ in operations.calls])

    def test_unknown_create_requires_human_terminal_attestation_and_absence(self):
        operations = MemoryOperations()
        operations.unknown_create = True
        with self.assertRaisesRegex(
            core.CaptureRefused, "capture_failed_access_closed"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertEqual(
            [name for name, _ in operations.calls].count("human_create"), 1
        )
        self.assertEqual(
            [name for name, _ in operations.calls].count("human_remove"), 1
        )

    def test_changed_ip_after_create_closes_access_without_capture(self):
        operations = MemoryOperations()
        operations.change_public_ip = True
        with self.assertRaisesRegex(
            core.CaptureRefused, "capture_failed_access_closed"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertNotIn("capture", [name for name, _ in operations.calls])

    def test_capture_error_and_invalid_archive_always_close_access(self):
        for archive in (
            replace(MemoryOperations().archive, size=0),
            replace(MemoryOperations().archive, size=core.MAX_ARCHIVE_BYTES + 1),
            replace(MemoryOperations().archive, sha256="invalid"),
            replace(MemoryOperations().archive, source_revision="9a2399212488"),
            replace(MemoryOperations().archive, custom_format_complete=False),
            replace(MemoryOperations().archive, encrypted_descriptor_bound=False),
            None,
        ):
            operations = MemoryOperations()
            operations.archive = archive
            with (
                self.subTest(archive=archive),
                self.assertRaisesRegex(
                    core.CaptureRefused, "capture_failed_access_closed"
                ),
            ):
                core.execute_after_human_gate(inputs(), operations)
            self.assertEqual(operations.rules, operations.initial)
            self.assertIsNone(operations.published)

    def test_capture_exception_and_interrupt_are_redacted_after_cleanup(self):
        for exception in (RuntimeError("private diagnostic"), KeyboardInterrupt()):
            operations = MemoryOperations()

            def fail():
                raise exception

            operations.capture_hook = fail
            with (
                self.subTest(exception=type(exception)),
                self.assertRaisesRegex(
                    core.CaptureRefused, "^capture_failed_access_closed$"
                ),
            ):
                core.execute_after_human_gate(inputs(), operations)
            self.assertEqual(operations.rules, operations.initial)

    def test_changed_existing_rule_is_not_repaired_and_no_success_published(self):
        operations = MemoryOperations()
        operations.change_preexisting = True
        with self.assertRaisesRegex(
            core.CaptureRefused, "cleanup_failed_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertNotIn(inputs().rule.name, operations.rules)
        self.assertNotEqual(operations.rules, operations.initial)
        self.assertIsNone(operations.published)

    def test_human_refusal_to_remove_changed_rule_is_not_success(self):
        operations = MemoryOperations()
        operations.change_new_rule = True
        with self.assertRaisesRegex(
            core.CaptureRefused, "cleanup_failed_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertIn("human_remove", [name for name, _ in operations.calls])
        self.assertIn(inputs().rule.name, operations.rules)
        self.assertIsNone(operations.published)

    def test_stuck_capture_does_not_prevent_rule_cleanup(self):
        operations = MemoryOperations()
        operations.failures.add("cancel")
        with self.assertRaisesRegex(
            core.CaptureRefused, "cleanup_failed_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertIn("human_remove", [name for name, _ in operations.calls])

    def test_failed_rule_deletion_is_actionable_and_never_success(self):
        operations = MemoryOperations()
        operations.failures.add("human_remove")
        with self.assertRaisesRegex(
            core.CaptureRefused, "cleanup_failed_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertIn(inputs().rule.name, operations.rules)
        self.assertIsNone(operations.published)

    def test_unknown_delete_is_not_success_even_when_rule_is_absent(self):
        operations = MemoryOperations()
        operations.unknown_delete_closed = True
        with self.assertRaisesRegex(
            core.CaptureRefused, "cleanup_failed_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertIsNone(operations.published)
        self.assertEqual(
            [name for name, _ in operations.calls].count("human_remove"), 1
        )

    def test_protocol_and_double_have_no_firewall_mutation_method(self):
        for candidate in (core.Operations, MemoryOperations):
            self.assertFalse(hasattr(candidate, "create_exact_rule"))
            self.assertFalse(hasattr(candidate, "delete_exact_rule"))

    def test_pending_operation_attestation_cannot_be_replaced_by_absence(self):
        operations = MemoryOperations()
        operations.closure = core.HumanClosure(False, True)
        with self.assertRaisesRegex(
            core.CaptureRefused, "cleanup_failed_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertIsNone(operations.published)

    def test_missing_or_nonboolean_removal_attestation_refuses(self):
        for closure in (
            None,
            core.HumanClosure(True, False),
            core.HumanClosure(1, True),
        ):
            operations = MemoryOperations()
            operations.closure = closure
            with (
                self.subTest(closure=closure),
                self.assertRaisesRegex(
                    core.CaptureRefused, "cleanup_failed_human_action_required"
                ),
            ):
                core.execute_after_human_gate(inputs(), operations)
            self.assertIsNone(operations.published)

    def test_creation_confirmation_required_before_capture(self):
        for confirmation in (False, None, 1):
            operations = MemoryOperations()
            operations.creation_confirmed = confirmation
            with self.assertRaisesRegex(
                core.CaptureRefused, "capture_failed_access_closed"
            ):
                core.execute_after_human_gate(inputs(), operations)
            self.assertNotIn("capture", [name for name, _ in operations.calls])
            self.assertIn("human_remove", [name for name, _ in operations.calls])

    def test_partial_instruction_failure_still_requests_human_removal(self):
        operations = MemoryOperations()
        operations.failures.add("instructions")
        with self.assertRaisesRegex(
            core.CaptureRefused, "capture_failed_access_closed"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertIn("human_remove", [name for name, _ in operations.calls])
        self.assertNotIn("human_create", [name for name, _ in operations.calls])

    def test_human_waits_share_original_window_and_do_not_reset_clock(self):
        operations = MemoryOperations()
        operations.creation_seconds = 119
        operations.capture_seconds = 900
        operations.removal_seconds = 600
        result = core.execute_after_human_gate(inputs(), operations)
        self.assertGreater(result["elapsed_seconds"], 1619)
        self.assertLess(result["elapsed_seconds"], 1800)
        operations = MemoryOperations()
        operations.removal_seconds = 1801
        with self.assertRaisesRegex(
            core.CaptureRefused, "access_window_exceeded_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertIsNone(operations.published)

    def test_slow_creation_prevents_capture_but_not_removal(self):
        operations = MemoryOperations()
        operations.creation_seconds = 800
        with self.assertRaisesRegex(
            core.CaptureRefused, "capture_failed_access_closed"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertNotIn("capture", [name for name, _ in operations.calls])
        self.assertIn("human_remove", [name for name, _ in operations.calls])

    def test_slow_portal_creation_and_removal_fit_original_access_window(self):
        operations = MemoryOperations()
        operations.creation_seconds = 599
        operations.capture_seconds = 400
        operations.removal_seconds = 599
        result = core.execute_after_human_gate(inputs(), operations)
        timeouts = dict(operations.calls)
        self.assertEqual(timeouts["human_create"], 600)
        self.assertEqual(timeouts["human_remove"], 600)
        self.assertLess(timeouts["capture"], 511)
        self.assertGreater(result["elapsed_seconds"], 1598)
        self.assertLess(result["elapsed_seconds"], 1800)
        self.assertEqual(operations.rules, operations.initial)

    def test_capture_exhausting_its_budget_still_reserves_ten_minute_removal(self):
        operations = MemoryOperations()
        original = operations.capture_to_exclusive_encrypted_descriptor

        def use_budget(_inputs, timeout, max_bytes):
            operations.capture_seconds = timeout - 1
            return original(_inputs, timeout, max_bytes)

        operations.capture_to_exclusive_encrypted_descriptor = use_budget
        operations.removal_seconds = 599
        result = core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(dict(operations.calls)["human_remove"], 600)
        self.assertLess(result["elapsed_seconds"], 1800)
        self.assertEqual(operations.rules, operations.initial)

    def test_handoff_expires_during_creation_without_capture_or_renewal(self):
        operations = MemoryOperations()
        operations.creation_seconds = 120
        value = replace(
            inputs(),
            handoff_created_at=NOW - timedelta(seconds=2579),
            handoff_expires_at=NOW + timedelta(seconds=121),
        )
        with self.assertRaisesRegex(
            core.CaptureRefused, "capture_failed_access_closed"
        ):
            core.execute_after_human_gate(value, operations)
        self.assertNotIn("capture", [name for name, _ in operations.calls])
        self.assertIn("human_remove", [name for name, _ in operations.calls])

    def test_closure_readback_failure_does_not_hide_removal_request(self):
        operations = MemoryOperations()
        operations.capture_hook = lambda: operations.failures.add("list")
        with self.assertRaisesRegex(
            core.CaptureRefused, "cleanup_failed_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertIn("human_remove", [name for name, _ in operations.calls])
        self.assertIsNone(operations.published)

    def test_elapsed_capture_timeout_and_receipt_expiry_do_not_block_cleanup(self):
        operations = MemoryOperations()
        operations.capture_seconds = 1600
        with self.assertRaisesRegex(
            core.CaptureRefused, "capture_failed_access_closed"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertIsNone(operations.published)

    def test_excessive_access_duration_is_reported_even_after_closure(self):
        operations = MemoryOperations()
        operations.capture_seconds = 1801
        with self.assertRaisesRegex(
            core.CaptureRefused, "access_window_exceeded_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertIsNone(operations.published)

    def test_clock_anomaly_after_creation_does_not_block_cleanup(self):
        for offset in (-10, 10):
            operations = MemoryOperations()
            operations.capture_hook = lambda: setattr(operations, "wall_offset", offset)
            with (
                self.subTest(offset=offset),
                self.assertRaisesRegex(
                    core.CaptureRefused, "cleanup_failed_human_action_required"
                ),
            ):
                core.execute_after_human_gate(inputs(), operations)
            self.assertEqual(operations.rules, operations.initial)
            self.assertIsNone(operations.published)

    def test_duplicate_inventory_records_are_not_collapsed(self):
        rule = inputs().rule
        with self.assertRaisesRegex(core.CaptureRefused, "duplicate_rule_names"):
            core.rules_by_name((rule, rule))

    def test_transient_cleanup_clock_failure_remains_latched(self):
        operations = MemoryOperations()
        original_cancel = operations.cancel_capture_and_reap
        original_remove = operations.await_human_rule_removal

        def cancel(timeout):
            original_cancel(timeout)
            operations.wall_offset = 10

        def remove(rule, timeout):
            operations.wall_offset = 0
            return original_remove(rule, timeout)

        operations.cancel_capture_and_reap = cancel
        operations.await_human_rule_removal = remove
        with self.assertRaisesRegex(
            core.CaptureRefused, "cleanup_failed_human_action_required"
        ):
            core.execute_after_human_gate(inputs(), operations)
        self.assertEqual(operations.rules, operations.initial)
        self.assertIsNone(operations.published)

    def test_metadata_failure_occurs_only_after_access_is_closed(self):
        operations = MemoryOperations()
        operations.failures.add("publish")
        with self.assertRaisesRegex(
            core.CaptureRefused, "snapshot_check_failed_no_acceptance"
        ) as raised:
            core.execute_after_human_gate(inputs(), operations)
        self.assertNotIn("invented private diagnostic", str(raised.exception))
        self.assertEqual(operations.rules, operations.initial)
        self.assertIsNone(operations.published)

    def test_adapter_cannot_smuggle_private_text_in_refusal_class(self):
        operations = MemoryOperations()

        def fail(_inputs, timeout):
            raise core.CaptureRefused("invented private diagnostic must not escape")

        operations.revalidate_human_gate = fail
        with self.assertRaisesRegex(
            core.CaptureRefused, "snapshot_check_failed_no_acceptance"
        ) as raised:
            core.execute_after_human_gate(inputs(), operations)
        self.assertNotIn("invented private diagnostic", str(raised.exception))
        self.assertFalse(operations.created)

    def test_refusal_arguments_and_subclass_are_rechecked_at_public_boundary(self):
        operations = MemoryOperations()

        class PrivateFailure(core.CaptureRefused):
            def __str__(self):
                return "invented private diagnostic must not escape"

        def fail(_inputs, timeout):
            raise PrivateFailure("clock")

        operations.revalidate_human_gate = fail
        with self.assertRaisesRegex(
            core.CaptureRefused, "snapshot_check_failed_no_acceptance"
        ) as raised:
            core.execute_after_human_gate(inputs(), operations)
        self.assertNotIn("invented private diagnostic", str(raised.exception))
        self.assertFalse(operations.created)


if __name__ == "__main__":
    unittest.main()
