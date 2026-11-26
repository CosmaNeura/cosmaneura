"""Offline tests for receipt markers and the carrier using synthetic Azure calls."""

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import carrier
import contract
import staging_rehearsal
from test_cto18_admission import markers

MASTER = contract.REVIEWED_SOURCE


class MarkerTests(unittest.TestCase):
    def test_g2_requires_exact_canonical_bytes(self):
        lines = markers()
        self.assertEqual(contract.parse_markers(lines)[1], "match")
        lines[4] = lines[4].replace(":false", ": false")
        self.assertEqual(contract.parse_markers(lines)[1], "differs")

    def test_incomplete_malformed_duplicate_and_error_markers(self):
        lines = markers()
        bad = [lines[:-1], lines[1:], lines[:5] + lines[6:]]
        bad.extend(lines + [marker] for marker in lines)
        bad.extend(
            lines[:index] + ["SYNTHETIC_PRIVATE_MARKER"] + lines[index + 1 :]
            for index in range(len(lines))
        )
        bad.extend(
            lines + [prefix + "=synthetic"]
            for prefix in (
                "RUN_ERROR",
                "ACCESS_ERROR",
                "PRECHECK_REFUSED",
            )
        )
        bad.append(lines[:7] + [lines[7].replace(",0", ",-1")] + lines[8:])
        for malformed in bad:
            with self.assertRaises(contract.Stop):
                contract.parse_markers(malformed)


class StagingTargetTests(unittest.TestCase):
    def setUp(self):
        self.args = {
            "subscription": carrier.PRODUCTION.subscription,
            "resource_group": "cosmaneura-staging",
            "job": "cosmaneura-staging-fixture-job",
            "container": "cosmaneura-staging-fixture-container",
            "workspace": "12345678-1234-1234-1234-123456789abd",
            "image": "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:"
            + "1" * 64,
        }

    def test_accepts_staging_target_in_shared_subscription(self):
        target = staging_rehearsal.staging_target(SimpleNamespace(**self.args))
        self.assertEqual(
            target,
            carrier.Target(
                carrier.PRODUCTION.subscription,
                self.args["resource_group"],
                self.args["job"],
                self.args["workspace"],
                self.args["image"],
                self.args["container"],
            ),
        )

    def test_refuses_production_values_in_either_subscription(self):
        for subscription in (
            carrier.PRODUCTION.subscription,
            "12345678-1234-1234-1234-123456789abc",
        ):
            for field, value in (
                ("resource_group", carrier.PRODUCTION.group),
                ("job", carrier.PRODUCTION.job),
                ("container", carrier.PRODUCTION.container),
                ("job", carrier.PRODUCTION_APP),
                ("workspace", carrier.PRODUCTION.workspace),
                ("image", carrier.PRODUCTION.image),
                ("resource_group", "cosmaneura-staging-prodlike"),
                ("job", "cosmaneura-staging-prodlike"),
                ("container", "cosmaneura-staging-prodlike"),
            ):
                with self.subTest(subscription=subscription, field=field, value=value):
                    args = SimpleNamespace(
                        **(self.args | {"subscription": subscription, field: value})
                    )
                    with self.assertRaisesRegex(
                        contract.Stop, "^staging_target_required$"
                    ):
                        staging_rehearsal.staging_target(args)


class FakeAzureTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state_path = directory / "state.json"
        fake = directory / "az"
        shutil.copyfile(contract.PACKAGE / "tests" / "fake_az.py", fake)
        fake.write_text(f"#!{sys.executable}\n" + fake.read_text())
        fake.chmod(stat.S_IRWXU)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                    "FAKE_AZ_STATE": str(self.state_path),
                },
            )
        )
        self.now = 0.0

    def configure(
        self, scenario="pass", *, target=carrier.PRODUCTION, lines=None, busy=False
    ):
        self.now = 0.0
        executors = {target.job + "-busy": {"status": None}} if busy else {}
        self.state_path.write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "job": target.job,
                    "image": target.image,
                    "container": target.container,
                    "staging": target != carrier.PRODUCTION,
                    "starts": 0,
                    "stops": [],
                    "log_reads": 0,
                    "executions": executors,
                    "lines": markers() if lines is None else lines,
                }
            )
        )
        return carrier.Carrier(target, clock=lambda: self.now, sleep=self.advance)

    def advance(self, seconds):
        self.now += seconds

    def state(self):
        return json.loads(self.state_path.read_text())

    def test_production_success_and_finished_log_fallback(self):
        subject = self.configure("logs_delayed")
        receipt = carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        contract.verify_receipt(receipt, MASTER, 42)
        self.assertEqual(self.state()["starts"], 1)
        self.assertEqual(self.state()["stops"], [])
        self.assertEqual(self.state()["log_reads"], 2)

    def test_busy_digest_traffic_retry_incomplete_listing_stop_before_start(self):
        for scenario, code in (
            ("busy", "migration_job_busy"),
            ("digest", "traffic_image_drift"),
            ("traffic", "traffic_not_fully_verified"),
            ("retry", "retry_must_be_zero"),
            ("paginated", "execution_list_incomplete"),
        ):
            with self.subTest(scenario=scenario):
                subject = self.configure(scenario, busy=scenario == "busy")
                with self.assertRaisesRegex(contract.Stop, code):
                    carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
                self.assertEqual(self.state()["starts"], 0)

    def test_checkout_sha_mismatch_stops_before_start(self):
        subject = self.configure()
        with self.assertRaisesRegex(contract.Stop, "^checkout_sha$"):
            carrier.run_production(subject, MASTER, 42, checkout_sha="a" * 40)
        self.assertEqual(self.state()["starts"], 0)

    def test_production_results_are_identical_for_two_fake_heads(self):
        payload = contract.encoded_inputs(MASTER)
        results = []
        for checkout_sha in ("a" * 40, "b" * 40):
            with self.subTest(checkout_sha=checkout_sha):
                subject = self.configure()
                with patch.object(
                    carrier, "encoded_inputs", return_value=payload
                ) as encode:
                    receipt = carrier.run_production(
                        subject, checkout_sha, 42, checkout_sha=checkout_sha
                    )
                    encode.assert_called_once_with(checkout_sha)
                contract.verify_receipt(receipt, checkout_sha, 42)
                results.append(
                    (receipt["status"], receipt["access_counts"], self.state())
                )
        self.assertEqual(len(results), 2)
        self.assertEqual(*results)

    def test_terminal_failure_no_stop_or_retry(self):
        for scenario in ("Failed", "Stopped", "Degraded"):
            subject = self.configure(scenario)
            with self.assertRaisesRegex(contract.Stop, "execution_terminal_failure"):
                carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
            self.assertEqual(self.state()["starts"], 1)
            self.assertEqual(self.state()["stops"], [])

    def test_cleanup_waits_and_never_starts_twice(self):
        expected = f"{carrier.PRODUCTION.job}-fake1"
        for scenario, code in (
            ("lost", "start_response_lost_execution_terminal"),
            ("poll_error", "execution_status_unavailable_stopped"),
            ("timeout", "execution_timed_out_stopped"),
            ("unknown", "execution_unknown_status_stopped"),
            ("stop_failed", "incident_stop_failed_terminal"),
            ("stop_unconfirmed", "incident_stop_unconfirmed"),
        ):
            with self.subTest(scenario=scenario):
                subject = self.configure(scenario)
                with self.assertRaisesRegex(contract.Stop, code):
                    carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
                self.assertEqual(self.state()["starts"], 1)
                self.assertEqual(self.state()["stops"], [expected])
                if scenario != "stop_unconfirmed":
                    self.assertEqual(
                        self.state()["executions"][expected]["status"], "Stopped"
                    )

    def test_unmatched_lost_response_never_stops_another_execution(self):
        subject = self.configure("unmatched")
        with self.assertRaisesRegex(contract.Stop, "incident_start_unresolved"):
            carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        self.assertEqual(self.state()["starts"], 1)
        self.assertEqual(self.state()["stops"], [])

    def test_template_drift_multiple_executions_log_errors_and_incomplete_logs(self):
        for scenario, code, lines in (
            ("drift", "migration_template_changed", None),
            ("two_executions", "execution_count_or_status_changed", None),
            ("logs_error", "azure_command_failed", None),
            ("logs_partial", "analytics_response", None),
            ("logs_missing", "finished_logs_missing", None),
            ("pass", "incomplete_or_extra_log", markers()[:-1]),
        ):
            with self.subTest(scenario=scenario):
                subject = self.configure(scenario, lines=lines)
                with self.assertRaisesRegex(contract.Stop, code):
                    carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
                self.assertEqual(self.state()["starts"], 1)

    def test_staging_rehearsal_payload_template_timeout_and_finished_logs(self):
        target = staging_rehearsal.staging_target(
            SimpleNamespace(
                subscription="12345678-1234-1234-1234-123456789abc",
                resource_group="cosmaneura-staging",
                job="cosmaneura-staging-fixture",
                container="cosmaneura-staging-fixture",
                workspace="12345678-1234-1234-1234-123456789abd",
                image="cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:"
                + "1" * 64,
            )
        )
        lines = [staging_rehearsal.SYNTHETIC_START, staging_rehearsal.SYNTHETIC_DONE]
        result = staging_rehearsal.rehearsal(self.configure(target=target, lines=lines))
        self.assertEqual(result["payload_bytes"], 92412)
        self.assertEqual(self.state()["starts"], 2)
        self.assertEqual(self.state()["stops"], [target.job + "-fake2"])
        with self.assertRaisesRegex(contract.Stop, "rehearsal_finished_log_invalid"):
            staging_rehearsal.rehearsal(
                self.configure(target=target, lines=lines + lines)
            )
        self.assertEqual(self.state()["starts"], 1)
        with self.assertRaisesRegex(contract.Stop, "retry_must_be_zero"):
            staging_rehearsal.rehearsal(self.configure("retry", target=target))
        self.assertEqual(self.state()["starts"], 0)
        with self.assertRaisesRegex(contract.Stop, "staging_target_required"):
            staging_rehearsal.staging_target(
                SimpleNamespace(
                    subscription=carrier.PRODUCTION.subscription,
                    resource_group=carrier.PRODUCTION.group,
                )
            )


if __name__ == "__main__":
    unittest.main()
