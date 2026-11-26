"""Offline tests. Only synthetic workflows, plan output, and fake Azure calls."""

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import admission
import carrier
import contract
import render_plan
import staging_rehearsal

MASTER = contract.REVIEWED_SOURCE
EXECUTION = f"{carrier.PRODUCTION.job}-1234"


def markers():
    return [
        "RUNNER_SHA_OK=1",
        "RUN_START=1",
        "BUNDLE_VERIFIED=97",
        "URL_SHAPE=" + ",".join(f"{key}:1" for key in contract.runner.SHAPE_KEYS),
        "PRECHECK_JSON=" + contract.G2,
        "PRECHECK_EXIT=1",
        "ACCESS_LOCK_TIMEOUT_MS=4000",
        *(f"ACCESS_COUNT={label},0" for label in contract.runner.ACCESS_LABELS),
        "ACCESS_ROWS=9",
        "RUN_DONE=1",
    ]


class PackageAndMarkersTests(unittest.TestCase):
    def test_all_bytes_manifest_bootstrap_and_sql(self):
        contract.verify_package()

    def test_pinned_payload(self):
        scripts, migrations = contract.candidate_bundle(MASTER)
        self.assertEqual(len(scripts), 2)
        self.assertEqual(len(migrations), 97)
        one = contract.encoded_inputs(MASTER)
        two = contract.encoded_inputs(MASTER)
        self.assertEqual(len(one["CORE_MIGRATIONS_Z"]), 92412)
        self.assertNotEqual(one["CORE_BUNDLE_Z"], two["CORE_BUNDLE_Z"])

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


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.workflow = "infra_production_plan_local.yml"
        self.env = {
            "GITHUB_REPOSITORY": admission.REPOSITORY,
            "GITHUB_WORKFLOW_REF": f"{admission.REPOSITORY}/{admission.WORKFLOW_DIR}"
            f"{self.workflow}@{admission.REF}",
            "GITHUB_WORKFLOW_SHA": MASTER,
            "GITHUB_SHA": MASTER,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": admission.REF,
            "GITHUB_ACTOR": admission.FOUNDER,
            "TRIGGERING_ACTOR": admission.FOUNDER,
            "ADMITTED_SHA": MASTER,
        }
        self.master = {
            "ref": admission.REF,
            "object": {"type": "commit", "sha": MASTER},
        }

    def test_admission_exact_context(self):
        self.assertEqual(
            admission.check_context(
                self.workflow, self.env, lambda _: self.master, admitted=True
            ),
            MASTER,
        )
        for key in self.env:
            with self.subTest(key=key):
                with self.assertRaises(contract.Stop):
                    admission.check_context(
                        self.workflow,
                        {**self.env, key: "wrong"},
                        lambda _: self.master,
                        admitted=True,
                    )
        del self.env["ADMITTED_SHA"]
        with self.assertRaisesRegex(contract.Stop, "admission_metadata"):
            admission.check_context(
                self.workflow, self.env, lambda _: self.master, admitted=True
            )

    def test_fresh_master_lookup_stale_and_failed(self):
        self.master["object"]["sha"] = "1" * 40
        with self.assertRaisesRegex(contract.Stop, "stale_master"):
            admission.check_context(self.workflow, self.env, lambda _: self.master)
        with patch.dict(os.environ, self.env, clear=True):
            with patch.object(admission, "api", side_effect=OSError("synthetic")):
                with patch.object(sys, "argv", ["admission", self.workflow]):
                    with patch("sys.stdout", io.StringIO()) as output:
                        self.assertEqual(admission.main(), 1)
                        self.assertNotIn("synthetic", output.getvalue())

    def test_receipt_provenance_and_digest(self):
        receipt = contract.receipt_header(MASTER, 42, EXECUTION, "database-url")
        receipt.update(
            template_unchanged=True,
            template_sha256="2" * 64,
            retry_limit=0,
            replica_timeout_seconds=450,
            execution_count=1,
            g2_shape="match",
            access_counts=contract.parse_markers(markers())[0],
        )
        run = {
            "path": admission.WORKFLOW_DIR + contract.CHECKS_WORKFLOW,
            "event": "workflow_dispatch",
            "head_branch": "master",
            "head_sha": MASTER,
            "repository": {"full_name": admission.REPOSITORY},
            "head_repository": {"full_name": admission.REPOSITORY},
            "actor": {"login": "JohnClem"},
            "triggering_actor": {"login": "JohnClem"},
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
            "id": 42,
        }
        jobs = {
            "total_count": 2,
            "jobs": [
                {"name": "Authorize production checks", "conclusion": "success"},
                {"name": "Execute production checks", "conclusion": "success"},
            ],
        }
        artifact = {
            "id": 48,
            "name": f"production-checks-receipt-42-{MASTER}",
            "expired": False,
        }

        def lookup(path, *, archive=False):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as zipped:
                zipped.writestr(admission.RECEIPT_FILE, json.dumps(receipt))
            content = buffer.getvalue()
            if archive:
                return content
            if path.endswith("jobs?per_page=10"):
                return jobs
            if path.endswith("artifacts?per_page=10"):
                return {
                    "total_count": 1,
                    "artifacts": [
                        {"digest": "sha256:" + contract.sha256(content), **artifact}
                    ],
                }
            self.assertIn(f"head_sha={MASTER}", path)
            return {"workflow_runs": [run]}

        admission.require_production_receipt(MASTER, lookup)
        with self.assertRaisesRegex(contract.Stop, "receipt_missing"):
            admission.require_production_receipt(
                MASTER, lambda _: {"workflow_runs": []}
            )
        for field, wrong in (
            ("head_sha", "f" * 40),
            ("path", "staging.yml"),
            ("conclusion", "failure"),
            ("run_attempt", 2),
            ("triggering_actor", {"login": "other"}),
        ):
            with patch.dict(run, {field: wrong}):
                with self.assertRaisesRegex(contract.Stop, "receipt_run_provenance"):
                    admission.require_production_receipt(MASTER, lookup)
        with patch.dict(jobs["jobs"][1], conclusion="skipped"):
            with self.assertRaisesRegex(contract.Stop, "receipt_jobs"):
                admission.require_production_receipt(MASTER, lookup)
        with patch.dict(artifact, digest="sha256:" + "0" * 64):
            with self.assertRaisesRegex(contract.Stop, "receipt_artifact_digest"):
                admission.require_production_receipt(MASTER, lookup)
        with patch.dict(artifact, expired=True):
            with self.assertRaisesRegex(contract.Stop, "receipt_artifact_metadata"):
                admission.require_production_receipt(MASTER, lookup)
        for field, wrong in (
            ("source", "synthetic"),
            ("candidate_sha", "f" * 40),
            ("workflow_sha", "f" * 40),
            ("retry_limit", False),
        ):
            with patch.dict(receipt, {field: wrong}):
                with self.assertRaisesRegex(contract.Stop, "receipt_contract"):
                    admission.require_production_receipt(MASTER, lookup)


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
        receipt = carrier.run_production(subject, MASTER, 42)
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
                    carrier.run_production(subject, MASTER, 42)
                self.assertEqual(self.state()["starts"], 0)

    def test_terminal_failure_no_stop_or_retry(self):
        for scenario in ("Failed", "Stopped", "Degraded"):
            subject = self.configure(scenario)
            with self.assertRaisesRegex(contract.Stop, "execution_terminal_failure"):
                carrier.run_production(subject, MASTER, 42)
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
                    carrier.run_production(subject, MASTER, 42)
                self.assertEqual(self.state()["starts"], 1)
                self.assertEqual(self.state()["stops"], [expected])
                if scenario != "stop_unconfirmed":
                    self.assertEqual(
                        self.state()["executions"][expected]["status"], "Stopped"
                    )

    def test_unmatched_lost_response_never_stops_another_execution(self):
        subject = self.configure("unmatched")
        with self.assertRaisesRegex(contract.Stop, "incident_start_unresolved"):
            carrier.run_production(subject, MASTER, 42)
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
                    carrier.run_production(subject, MASTER, 42)
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


class PlanRendererTests(unittest.TestCase):
    def test_values_diagnostics_and_instance_names_cannot_escape(self):
        catalog = render_plan.source_catalog(contract.REPO_ROOT / "infra")
        plan = {
            "format_version": "1.2",
            "resource_changes": [
                {
                    "address": 'azurerm_container_app.app["SYNTHETIC_PRIVATE_VALUE"]',
                    "change": {
                        "actions": ["update"],
                        "after": {"SYNTHETIC_PRIVATE_VALUE": "value"},
                    },
                },
            ],
            "diagnostics": ["SYNTHETIC_PRIVATE_DIAGNOSTICS"],
        }
        self.assertIn("azurerm_container_app.app", catalog)
        self.assertTrue(
            any(x.startswith("module.public_phi_observability.") for x in catalog)
        )
        summary = render_plan.render(plan, catalog)
        self.assertIn("azurerm_container_app.app", summary)
        self.assertIn("| update | 1 |", summary)
        self.assertNotIn("SYNTHETIC_PRIVATE", summary)

    def test_unrecognized_plan_refuses_without_partial_output(self):
        output = io.StringIO()
        diagnostics = io.StringIO()
        with (
            patch(
                "sys.stdin",
                io.StringIO(
                    '{"format_version":"1.2","resource_changes":[{"address":"leak.synthetic"}]}'
                ),
            ),
            patch("sys.stdout", output),
            patch("sys.stderr", diagnostics),
        ):
            self.assertEqual(render_plan.main(), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            diagnostics.getvalue(), "OpenTofu plan could not be rendered safely.\n"
        )


if __name__ == "__main__":
    unittest.main()
