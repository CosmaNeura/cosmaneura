"""Offline tests for receipt markers and the carrier using synthetic Azure calls."""

import ast
import io
import json
import os
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import carrier
import contract
import probe_image
import staging_rehearsal

MASTER = contract.REVIEWED_SOURCE
WORKFLOW = contract.REPO_ROOT / ".github/workflows/run_production_checks.yml"
STAGING_ARGS = {
    "subscription": carrier.PRODUCTION.subscription,
    "resource_group": "cosmaneura-staging",
    "job": "cosmaneura-staging-fixture-job",
    "container": "cn-stg-mig-fixture",
    "workspace": "12345678-1234-1234-1234-123456789abd",
    "image": "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:"
    + "1" * 64,
}


def markers():
    bits = {"prefer": 0, "ca_file": 0}
    return [
        "RUNNER_SHA_OK=1",
        "RUN_START=1",
        "BUNDLE_VERIFIED=97",
        "URL_SHAPE="
        + ",".join(f"{key}:{bits.get(key, 1)}" for key in contract.runner.SHAPE_KEYS),
        f"PRECHECK_JSON={contract.G2}",
        "PRECHECK_EXIT=1",
        "ACCESS_LOCK_TIMEOUT_MS=4000",
        *(f"ACCESS_COUNT={label},0" for label in contract.runner.ACCESS_LABELS),
        "ACCESS_ROWS=9",
        "RUN_DONE=1",
    ]


class ProbeTests(unittest.TestCase):
    @staticmethod
    def workflow_job(source, job):
        return re.split(r"\n  [\w-]+:\n", source.split(f"\n  {job}:\n", 1)[1])[0]

    def test_transition_receipt_steps_keep_automatic_rollback_independent(self):
        self.assertIn("production-checks-receipt", WORKFLOW.read_text())
        release = WORKFLOW.with_name("release_verified_candidate.yml").read_text()
        build = WORKFLOW.with_name("build_and_publish_local.yml").read_text()
        for name, source, count in (("release", release, 1), ("build", build, 2)):
            with self.subTest(workflow=name):
                steps = [
                    step for step in source.split("\n      - ") if "--receipt" in step
                ]
                self.assertEqual(len(steps), count)
                for step in steps:
                    self.assertIn("--running-source-sha-file", step)
                    self.assertNotIn("if: github.sha", step)
        for name in ("infra_production_plan_local", "infra_apply_local"):
            self.assertNotIn(
                "--running-source-sha-file",
                WORKFLOW.with_name(name + ".yml").read_text(),
            )
        self.assertNotIn(
            "--receipt", WORKFLOW.with_name("build_and_publish.yml").read_text()
        )

        release_steps = self.workflow_job(release, "publish-and-deploy").split(
            "\n      - "
        )
        checkout = next(
            step for step in release_steps if "uses: actions/checkout@" in step
        )
        self.assertRegex(checkout, r"(?m)^          fetch-depth: 0$")
        receipt = next(
            index for index, step in enumerate(release_steps) if "--receipt" in step
        )
        self.assertIn("Admit code-derived schema heads", release_steps[receipt - 1])
        self.assertIn("Record current production digests", release_steps[receipt + 1])
        deploy = next(
            index for index, step in enumerate(release_steps) if "id: deploy\n" in step
        )
        rollback = release_steps[deploy + 1]
        self.assertIn(
            "Verify healthcheck and ready-revision digests or roll back", rollback
        )
        self.assertIn("if: always() && steps.deploy.outcome != 'skipped'", rollback)
        self.assertIn("release_verified_candidate_ops.py rollback --baseline", rollback)
        self.assertNotIn("admission.py", rollback)
        self.assertNotIn("--receipt", rollback)

        ops = (
            contract.REPO_ROOT / "scripts/release_verified_candidate_ops.py"
        ).read_text()
        for node in ast.parse(ops).body:
            if isinstance(node, ast.FunctionDef) and node.name in {
                "rollback",
                "verify_ready",
            }:
                body = ast.unparse(node)
                self.assertNotIn("admission.py", body)
                self.assertNotIn("--receipt", body)

        for job, previous, following in (
            (
                "production-candidate",
                "uses: azure/login@",
                "Verify, publish and protect",
            ),
            ("deploy", "id: candidate", "Deploy the previously verified Core digest"),
        ):
            with self.subTest(job=job):
                steps = self.workflow_job(build, job).split("\n      - ")
                checkout = next(
                    step for step in steps if "uses: actions/checkout@" in step
                )
                self.assertRegex(checkout, r"(?m)^          fetch-depth: 0$")
                receipt = next(
                    index for index, step in enumerate(steps) if "--receipt" in step
                )
                read = receipt - 1
                self.assertIn(previous, steps[receipt - 2])
                self.assertIn("running-source-sha --output", steps[read])
                self.assertTrue(
                    any(
                        "uses: docker/login-action@" in step or "az acr login " in step
                        for step in steps[:read]
                    ),
                    "Registry authentication must precede the running revision read",
                )
                self.assertIn(following, steps[receipt + 1])

    def test_running_source_sha_mode_writes_revision_file(self):
        path = contract.REPO_ROOT / "scripts/release_verified_candidate_ops.py"
        output = Path(self.enterContext(tempfile.TemporaryDirectory())) / "running.json"
        with patch.object(sys, "path", sys.path.copy()):
            main = runpy.run_path(str(path))["main"]
        with (
            patch.dict(main.__globals__, running_source_sha=lambda: "a" * 40),
            patch.object(
                sys, "argv", [str(path), "running-source-sha", "--output", str(output)]
            ),
        ):
            self.assertEqual(main(), 0)
        self.assertEqual(
            json.loads(output.read_text()), {"running_source_sha": "a" * 40}
        )

    def test_button_records_carrier_reconciliation_in_job_history(self):
        source = WORKFLOW.read_text()
        for job in ("authorize-production", "checks"):
            with self.subTest(job=job):
                self.assertRegex(
                    self.workflow_job(source, job),
                    r"(?m)^    permissions:\n(?:      \S[^\n]*\n)*?      actions: read$",
                )
        self.assertIn(
            "- name: Run exactly one checked execution\n        id: carrier", source
        )
        self.assertIn("- name: Confirm no unresolved execution incident", source)
        self.assertIn("failure() && steps.carrier.outcome == 'failure'", source)
        self.assertIn("steps.carrier.outputs.execution_reconciled", source)
        self.assertIn('run: test "$EXECUTION_RECONCILED" = true', source)

    def test_runbook_link_restored(self):
        source = (
            contract.REPO_ROOT / "docs/production-migration-precheck.md"
        ).read_text()
        self.assertIn("[Hosted production checks](production-hosted-checks.md)", source)
        self.assertNotRegex(source, r"PR [23]\b")

    def test_workflow_orders_login_pull_probe_and_carrier(self):
        workflow = WORKFLOW.read_text()
        positions = [
            workflow.index(token)
            for token in (
                "uses: azure/login@",
                "az acr login --name cosmaneuraprodregistry",
                'containerd-snapshotter":true',
                "sudo systemctl restart docker",
                "docker pull --platform linux/amd64",
                "python3 ops/production-checks/probe_image.py",
                'CHECKOUT_SHA="$checkout_sha" python3 ops/production-checks/carrier.py',
            )
        ]
        self.assertEqual(positions, sorted(set(positions)))

    def test_pull_enables_containerd_image_store_before_docker_pull(self):
        pull = WORKFLOW.read_text().split(
            "name: Pull the pinned production image\n", 1
        )[1]
        pull = pull.split("\n      - name:", 1)[0]
        self.assertIn('containerd-snapshotter":true', pull)
        self.assertIn("sudo systemctl restart docker", pull)
        self.assertLess(
            pull.index('containerd-snapshotter":true'),
            pull.index("docker pull --platform linux/amd64"),
        )
        self.assertIn(
            "python3 ops/production-checks/probe_image.py", WORKFLOW.read_text()
        )
        # Probe contract stays unchanged: cached image, no network, no creds.
        self.assertIn("--pull=never", Path(probe_image.__file__).read_text())
        self.assertIn("--network=none", Path(probe_image.__file__).read_text())

    def test_checks_job_and_pre_carrier_step_timeouts(self):
        checks = WORKFLOW.read_text().split("\n  checks:\n", 1)[1]
        self.assertRegex(checks, r"(?m)^    timeout-minutes: 31$")
        for step, minutes in (
            (r"uses: actions/checkout@[^\n]+", 2),
            ("name: Reverify admission before production authentication", 1),
            ("name: Verify reviewed package and exact candidate before Azure login", 1),
            (r"uses: azure/login@[^\n]+", 2),
            ("name: Authenticate to production ACR with Azure OIDC", 2),
            ("name: Pull the pinned production image", 4),
            ("name: Probe the pinned production image", 2),
            ("name: Reverify fresh master before starting the check", 1),
        ):
            with self.subTest(step=step):
                self.assertRegex(
                    checks, rf"(?m)^      - {step}\n        timeout-minutes: {minutes}$"
                )

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
    def test_url_shape_requires_host_and_verify_full_without_prefer(self):
        for bit in (
            "host",
            "scheme",
            "user",
            "password",
            "port",
            "path",
            "keys",
            "verify_full",
            "prefer",
        ):
            with self.subTest(bit=bit):
                lines = markers()
                good = int(bit != "prefer")
                lines[3] = lines[3].replace(f"{bit}:{good}", f"{bit}:{1 - good}")
                with self.assertRaisesRegex(contract.Stop, "^invalid_log_marker$"):
                    contract.parse_markers(lines)
        lines = markers()
        lines[3] = lines[3].replace("ca_file:0", "ca_file:1")
        self.assertEqual(contract.parse_markers(lines)[1], "match")

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
    def test_accepts_staging_target_in_shared_subscription(self):
        target = staging_rehearsal.staging_target(SimpleNamespace(**STAGING_ARGS))
        self.assertEqual(
            target,
            carrier.Target(
                carrier.PRODUCTION.subscription,
                STAGING_ARGS["resource_group"],
                STAGING_ARGS["job"],
                STAGING_ARGS["workspace"],
                STAGING_ARGS["image"],
                STAGING_ARGS["container"],
            ),
        )

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
                        **(STAGING_ARGS | {"subscription": subscription, field: value})
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
                    "lines": markers()
                    if lines is None and target == carrier.PRODUCTION
                    else lines,
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
        with patch.object(subject, "job", wraps=subject.job) as job:
            receipt = carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        contract.verify_receipt(receipt, MASTER, 42)
        self.assertEqual(
            [c.kwargs["timeout"] for c in job.call_args_list if c.args[0] == "start"],
            [60],
        )
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
        self.assertEqual(self.state()["log_reads"], 3)

    def test_receipt_contract_rejects_invalid_preflight(self):
        receipt = carrier.run_production(
            self.configure(), MASTER, 42, checkout_sha=MASTER
        )
        preflight = receipt.pop("preflight")
        for malformed in (
            receipt,
            dict(receipt, preflight=[]),
            dict(receipt, preflight=dict(preflight, extra=True)),
            dict(receipt, preflight=dict(preflight, list_executions=1)),
            dict(receipt, preflight=dict(preflight, list_executions=False)),
            dict(receipt, preflight=dict(preflight, query_workspace=False)),
            dict(receipt, preflight=dict(preflight, show_execution=True)),
        ):
            with (
                self.subTest(preflight=malformed.get("preflight")),
                self.assertRaisesRegex(contract.Stop, "^receipt_contract$"),
            ):
                contract.verify_receipt(malformed, MASTER, 42)

    def test_busy_digest_traffic_retry_incomplete_listing_stop_before_start(self):
        for scenario, code in (
            ("busy", "migration_job_busy"),
            ("digest", "traffic_image_drift"),
            ("traffic", "traffic_not_fully_verified"),
            ("retry", "retry_must_be_zero"),
            ("foreign_execution_page", "preflight_list_denied"),
            ("duplicate_execution_page", "preflight_list_denied"),
        ):
            with self.subTest(scenario=scenario):
                subject = self.configure(
                    scenario, busy=scenario in {"busy", "duplicate_execution_page"}
                )
                with self.assertRaisesRegex(contract.Stop, code):
                    carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
                self.assertEqual(self.state()["starts"], 0)

    def test_preflight_shows_newest_execution(self):
        subject = self.configure("paginated")
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

    def test_log_next_link_and_foreign_host(self):
        subject = self.configure("log_pages")
        self.assertEqual(
            carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)["status"],
            "Succeeded",
        )
        subject = self.configure("foreign_log_page")
        with self.assertRaisesRegex(contract.Stop, "^analytics_response$"):
            carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)

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

    def test_button_admission_checks_first_attempt_and_sets_sha(self):
        candidate = MASTER
        repository = "CosmaNeura/cosmaneura"
        ref = "refs/heads/master"
        workflow_ref = f"{repository}/.github/workflows/run_production_checks.yml@{ref}"
        env = {
            "GITHUB_REPOSITORY": repository,
            "GITHUB_SHA": candidate,
            "GITHUB_WORKFLOW_SHA": candidate,
            "GITHUB_WORKFLOW_REF": workflow_ref,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": ref,
            "GITHUB_ACTOR": "JohnClem",
            "TRIGGERING_ACTOR": "JohnClem",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(self.state_path),
        }
        master = {"ref": ref, "object": {"type": "commit", "sha": candidate}}
        responses = {
            "git/ref/heads/master": master,
            "actions/workflows/run_production_checks.yml/runs?branch=master"
            "&event=workflow_dispatch&per_page=100&page=1": {
                "workflow_runs": [],
                "total_count": 0,
            },
        }
        expected_calls = [call(path) for path in responses]
        with (
            patch.dict(os.environ, env, clear=True),
            patch("admission.api", side_effect=responses.__getitem__) as lookup,
            patch("sys.stdout", io.StringIO()) as stdout,
        ):
            with patch.object(sys, "argv", ["carrier.py", "--output"]):
                self.assertEqual(carrier.main(), 0, stdout.getvalue())
            sha_output = self.state_path.read_text()
            self.assertEqual(sha_output, f"sha={candidate}\n")
            self.assertEqual(lookup.call_args_list, expected_calls)
            lookup.reset_mock()
            os.environ["ADMITTED_SHA"] = sha_output.strip().removeprefix("sha=")
            with patch.object(sys, "argv", ["carrier.py", "--admitted"]):
                self.assertEqual(carrier.main(), 0, stdout.getvalue())
            self.assertEqual(self.state_path.read_text(), sha_output)
            self.assertEqual(lookup.call_args_list, expected_calls)
            lookup.reset_mock()
            with patch.object(sys, "argv", ["carrier.py", "--output"]):
                os.environ["GITHUB_WORKFLOW_REF"] = (
                    f"{repository}/.github/workflows/infra_apply_local.yml@{ref}"
                )
                self.assertEqual(carrier.main(), 1)
                self.assertIn(
                    "Production checks stopped (run_context).", stdout.getvalue()
                )
                os.environ["GITHUB_WORKFLOW_REF"] = workflow_ref
                os.environ["GITHUB_RUN_ATTEMPT"] = "2"
                self.assertEqual(carrier.main(), 1)
                self.assertIn(
                    "Production checks stopped (run_attempt).", stdout.getvalue()
                )
            lookup.assert_not_called()
            self.assertEqual(self.state_path.read_text(), sha_output)

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

    def test_carrier_outputs_reconciliation_only_for_non_incident_stops(self):
        env = {
            "GITHUB_RUN_ID": "42",
            "GITHUB_SHA": MASTER,
            "CHECKOUT_SHA": MASTER,
            "GITHUB_OUTPUT": str(self.state_path),
        }
        with (
            patch.object(sys, "argv", ["carrier.py"]),
            patch.dict(os.environ, env),
            patch("sys.stdout", io.StringIO()),
        ):
            for code in (
                "incident_start_unresolved",
                "incident_stop_unconfirmed",
                "incident_stop_failed_terminal",
                "incident_execution_not_identified",
                "ambiguous_start",
                "execution_terminal_failure",
            ):
                with (
                    self.subTest(code=code),
                    patch("carrier.run_production", side_effect=contract.Stop(code)),
                ):
                    self.state_path.write_text("")
                    self.assertEqual(carrier.main(), 1)
                    expected = (
                        "execution_reconciled=true\n"
                        if code == "execution_terminal_failure"
                        else ""
                    )
                    self.assertEqual(self.state_path.read_text(), expected)

    def test_post_migration_markers_refuse_receipt(self):
        lines = markers()
        post_migration = json.loads(contract.G2)
        post_migration.update(
            migration_head="5708b4399768", pending_migrations=[], refusal_reasons=[]
        )
        lines[4] = "PRECHECK_JSON=" + json.dumps(post_migration, separators=(",", ":"))
        lines[5] = "PRECHECK_EXIT=0"
        subject = self.configure(lines=lines)
        directory = self.state_path.parent
        with (
            patch.object(sys, "argv", ["carrier.py"]),
            patch.dict(
                os.environ,
                GITHUB_RUN_ATTEMPT="1",
                GITHUB_RUN_ID="42",
                GITHUB_SHA=MASTER,
                CHECKOUT_SHA=MASTER,
                GITHUB_OUTPUT=str(directory / "output"),
                RUNNER_TEMP=str(directory),
            ),
            patch("carrier.Carrier", return_value=subject),
            patch("carrier.check_context", return_value=MASTER),
            patch("sys.stdout", io.StringIO()) as output,
        ):
            self.assertEqual(carrier.main(), 1)
            self.assertIn("g2_shape_differs", output.getvalue())
        self.assertFalse((directory / carrier.RECEIPT_FILE).exists())

    def test_template_drift_multiple_executions_and_incomplete_logs(self):
        for scenario, code, lines in (
            ("drift", "migration_template_changed", None),
            ("two_executions", "execution_count_or_status_changed", None),
            ("logs_partial", "analytics_response", None),
        ):
            with self.subTest(scenario=scenario):
                subject = self.configure(scenario, lines=lines)
                with self.assertRaisesRegex(contract.Stop, code):
                    carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
                self.assertEqual(self.state()["starts"], 1)

    def test_persistent_logs_error_retries_until_readback_budget(self):
        subject = self.configure("logs_error")
        with self.assertRaisesRegex(contract.Stop, "^azure_command_failed$") as raised:
            carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        self.assertEqual(getattr(raised.exception, "command_class", None), "readback")
        self.assertEqual(self.state()["starts"], 1)
        self.assertEqual(self.now, 240)
        self.assertEqual(self.state()["log_reads"], 48)

    def test_transient_logs_error_retries_then_succeeds(self):
        subject = self.configure("logs_transient")
        receipt = carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        contract.verify_receipt(receipt, MASTER, 42)
        self.assertEqual(self.state()["starts"], 1)
        self.assertEqual(self.state()["log_reads"], 3)
        self.assertEqual(self.state()["stops"], [])
        self.assertLess(self.now, 240)

    def test_persistent_list_error_retries_until_list_budget(self):
        subject = self.configure("list_error")
        with self.assertRaisesRegex(contract.Stop, "^azure_command_failed$") as raised:
            carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        self.assertEqual(getattr(raised.exception, "command_class", None), "list")
        self.assertEqual(self.state()["starts"], 1)
        self.assertEqual(self.now, 60)
        self.assertEqual(self.state()["list_reads"], 12)

    def test_transient_list_error_retries_then_succeeds(self):
        subject = self.configure("list_transient")
        receipt = carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        contract.verify_receipt(receipt, MASTER, 42)
        self.assertEqual(self.state()["starts"], 1)
        self.assertEqual(self.state()["list_reads"], 3)
        self.assertEqual(self.state()["stops"], [])
        self.assertLess(self.now, 60)

    def test_azure_command_failed_stop_print_includes_class_word_only(self):
        directory = self.state_path.parent
        subject = self.configure("logs_error")
        with (
            patch.object(sys, "argv", ["carrier.py"]),
            patch.dict(
                os.environ,
                GITHUB_RUN_ATTEMPT="1",
                GITHUB_RUN_ID="42",
                GITHUB_SHA=MASTER,
                CHECKOUT_SHA=MASTER,
                GITHUB_OUTPUT=str(directory / "output"),
                RUNNER_TEMP=str(directory),
            ),
            patch("carrier.Carrier", return_value=subject),
            patch("carrier.check_context", return_value=MASTER),
            patch("sys.stdout", io.StringIO()) as output,
        ):
            self.assertEqual(carrier.main(), 1)
            text = output.getvalue()
        self.assertEqual(
            text.strip(), "Production checks stopped (azure_command_failed:readback)."
        )
        self.assertNotRegex(text, r"[0-9a-f]{8,}")
        self.assertNotIn("SYNTHETIC_PRIVATE_DIAGNOSTIC", text)
        self.assertNotIn(carrier.PRODUCTION.job + "-fake", text)
        self.assertEqual(self.state()["starts"], 1)

    def test_partial_logs_fail_closed_after_four_minutes(self):
        subject = self.configure(lines=markers()[:-1])
        with self.assertRaisesRegex(contract.Stop, "^incomplete_or_extra_log$"):
            carrier.run_production(subject, MASTER, 42, checkout_sha=MASTER)
        self.assertEqual(self.now, 240)
        self.assertEqual(self.state()["log_reads"], 48)

    def test_staging_rehearsal_payload_template_timeout_and_finished_logs(self):
        target = staging_rehearsal.staging_target(SimpleNamespace(**STAGING_ARGS))
        result = staging_rehearsal.rehearsal(self.configure(target=target))
        lines = self.state()["lines"]
        self.assertEqual(len(lines), 18)
        self.assertEqual(lines, staging_rehearsal.SYNTHETIC_LINES)
        self.assertEqual(result["payload_bytes"], 92412)
        self.assertTrue(result["preflight"]["query_workspace"])
        self.assertEqual(self.state()["starts"], 2)
        self.assertEqual(self.state()["stops"], [target.job + "-fake2"])
        with self.assertRaisesRegex(contract.Stop, "rehearsal_finished_log_invalid"):
            staging_rehearsal.rehearsal(
                self.configure(target=target, lines=lines[1:2] + lines[:1] + lines[2:])
            )
        self.assertEqual(self.state()["starts"], 1)
        with self.assertRaisesRegex(contract.Stop, "retry_must_be_zero"):
            staging_rehearsal.rehearsal(self.configure("retry", target=target))
        self.assertEqual(self.state()["starts"], 0)


if __name__ == "__main__":
    unittest.main()
