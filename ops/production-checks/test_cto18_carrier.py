"""Offline tests for receipt markers and the carrier using synthetic Azure calls."""

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import carrier
import contract
import probe_image
import staging_rehearsal
from admission import INCIDENT_CODES, verify_checks_receipt
from test_cto18_admission import markers

MASTER = contract.REVIEWED_SOURCE
WORKFLOW = contract.REPO_ROOT / ".github/workflows/run_production_checks.yml"


class ProbeTests(unittest.TestCase):
    def test_workflow_orders_login_pull_probe_and_carrier(self):
        workflow = WORKFLOW.read_text()
        positions = [
            workflow.index(token)
            for token in (
                "uses: azure/login@",
                "az acr login --name cosmaneuraprodregistry",
                "docker pull --platform linux/amd64",
                "python3 ops/production-checks/probe_image.py",
                "python3 ops/production-checks/carrier.py",
            )
        ]
        self.assertEqual(positions, sorted(set(positions)))

    def test_registry_login_guard_allows_only_the_entra_token(self):
        block = WORKFLOW.read_text().split("<<'DOCKER_OIDC_ONLY'\n", 1)[1]
        guard = textwrap.dedent(block.split("DOCKER_OIDC_ONLY", 1)[0].rstrip())
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        docker = directory / "docker"
        docker.write_text("#!/bin/bash\nprintf 'fake docker called\\n'\n")
        docker.chmod(stat.S_IRWXU)
        env = os.environ | {"PATH": str(directory) + os.pathsep + os.environ["PATH"]}
        for username, code, stdout in (
            ("00000000-0000-0000-0000-000000000000", 0, "fake docker called\n"),
            ("synthetic-admin", 1, ""),
        ):
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    guard,
                    "guard",
                    "login",
                    "--username",
                    username,
                    "--password-stdin",
                    "cosmaneuraprodregistry.azurecr.io",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual((result.returncode, result.stdout), (code, stdout))

    def test_cached_probe_accepts_only_exact_output(self):
        staging = (
            "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:"
            + "1" * 64
        )
        for argv, image in [([], contract.IMAGE), (["--image", staging], staging)]:
            for stdout, stderr, exit_code in (
                (b"RUNTIME_SUPPORTED=1\n", b"", 0),
                (b"RUNTIME_SUPPORTED=1\nextra\n", b"", 1),
                (b"RUNTIME_SUPPORTED=1\n", b"unexpected\n", 1),
                (b"", b"", 1),
            ):
                with (
                    self.subTest(argv=argv, stdout=stdout, stderr=stderr),
                    patch(
                        "probe_image.subprocess.run",
                        return_value=SimpleNamespace(stdout=stdout, stderr=stderr),
                    ) as docker,
                    patch("sys.stdout", io.StringIO()),
                ):
                    self.assertEqual(probe_image.main(argv), exit_code)
                    command = docker.call_args.args[0]
                    self.assertIn(image, command)
                    self.assertIn("--pull=never", command)
                    self.assertIn("--network=none", command)
                    self.assertTrue(docker.call_args.kwargs["check"])
        with (
            patch(
                "probe_image.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "docker"),
            ),
            patch("sys.stdout", io.StringIO()),
        ):
            self.assertEqual(probe_image.main([]), 1)
        for bad in (contract.IMAGE, staging.split("@")[0] + ":latest"):
            with (
                patch("probe_image.subprocess.run") as docker,
                patch("sys.stderr", io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                probe_image.main(["--image", bad])
            docker.assert_not_called()


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

    def test_accepts_real_staging_target(self):
        job = "cn-stg-mig-9b810f5707655bc74506"
        args = SimpleNamespace(
            subscription="bab2a709-1fcb-4c87-a9fc-802f7477ec13",
            resource_group="cosmaneura-staging",
            job=job,
            container=job,
            workspace="f170125a-69a8-4fe5-9a62-d7d366814c04",
            image="cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:"
            "3bf67cbcf1e31999621268f62ad0266005fd651b1a6b700ac794411b14c090db",
        )
        self.assertEqual(staging_rehearsal.staging_target(args).job, job)

    def test_refuses_production_values_in_either_subscription(self):
        for subscription in (
            carrier.PRODUCTION.subscription,
            "12345678-1234-1234-1234-123456789abc",
        ):
            for field, value in (
                ("resource_group", carrier.PRODUCTION.group),
                ("resource_group", "cosmaneura-staging-x"),
                ("job", carrier.PRODUCTION.job),
                ("container", carrier.PRODUCTION.container),
                ("job", carrier.PRODUCTION_APP),
                ("container", carrier.PRODUCTION_APP),
                ("workspace", carrier.PRODUCTION.workspace),
                ("image", carrier.PRODUCTION.image),
                ("resource_group", "cosmaneura-staging-prodlike"),
                ("job", "cosmaneura-staging-prodlike"),
                ("container", "cosmaneura-staging-prodlike"),
                ("job", "cn-stg-prodlike"),
                ("container", "cn-stg-prodlike"),
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
        verify_checks_receipt(receipt, MASTER, 42)
        self.assertEqual(
            receipt["preflight"],
            {
                "list_executions": True,
                "show_execution": False,
                "no_prior_execution": True,
                "query_workspace": True,
            },
        )
        self.assertEqual(self.state()["starts"], 1)
        self.assertEqual(self.state()["stops"], [])
        self.assertEqual(self.state()["log_reads"], 2)

    def test_busy_digest_traffic_retry_incomplete_listing_stop_before_start(self):
        for scenario, code in (
            ("busy", "migration_job_busy"),
            ("digest", "traffic_image_drift"),
            ("traffic", "traffic_not_fully_verified"),
            ("retry", "retry_must_be_zero"),
            ("paginated", "preflight_list_denied"),
        ):
            with self.subTest(scenario=scenario):
                subject = self.configure(scenario, busy=scenario == "busy")
                with self.assertRaisesRegex(contract.Stop, code):
                    carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
                self.assertEqual(self.state()["starts"], 0)

    def test_preflight_shows_newest_execution(self):
        subject = self.configure()
        state = self.state()
        newest = subject.target.job + "-newest"
        state["executions"] = {
            subject.target.job + "-older": {
                "status": "Succeeded",
                "start": "2026-01-01T00:00:00Z",
            },
            newest: {"status": "Succeeded", "start": "2026-01-02T00:00:00Z"},
        }
        self.state_path.write_text(json.dumps(state))
        receipt = carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        self.assertEqual(self.state()["preflight_shows"], [newest])
        self.assertTrue(receipt["preflight"]["show_execution"])
        self.assertFalse(receipt["preflight"]["no_prior_execution"])

    def test_preflight_denials_never_start_or_stop(self):
        for action in ("list", "show", "query"):
            with self.subTest(action=action):
                subject = self.configure(f"{action}_denied", busy=action == "show")
                with self.assertRaisesRegex(
                    contract.Stop, f"^preflight_{action}_denied$"
                ):
                    carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
                self.assertEqual(self.state()["starts"], 0)
                self.assertEqual(self.state()["stops"], [])

    def test_carrier_only_confirms_reconciled_failures(self):
        output = self.state_path
        for code in (*INCIDENT_CODES, "preflight_list_denied"):
            output.write_text("")
            with (
                self.subTest(code=code),
                patch.dict(
                    os.environ,
                    GITHUB_RUN_ID="42",
                    GITHUB_SHA=MASTER,
                    CHECKOUT_SHA=MASTER,
                    GITHUB_OUTPUT=str(output),
                ),
                patch("carrier.run_production", side_effect=contract.Stop(code)),
                patch("sys.stdout", io.StringIO()),
            ):
                self.assertEqual(carrier.main(), 1)
            expected = "" if code in INCIDENT_CODES else "execution_reconciled=true\n"
            self.assertEqual(output.read_text(), expected)

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
                verify_checks_receipt(receipt, checkout_sha, 42)
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
        self.assertTrue(result["preflight"]["query_workspace"])
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
