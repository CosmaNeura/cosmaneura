"""Offline tests for admission, the pinned runner package, and plan rendering."""

import io
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import admission
import contract
import render_plan

MASTER = contract.REVIEWED_SOURCE
EXECUTION = f"{contract.PRODUCTION_JOB}-1234"


class PackageAndMarkersTests(unittest.TestCase):
    def test_all_bytes_manifest_bootstrap_and_sql(self):
        contract.verify_package()
        manifest = (contract.PACKAGE / "SHA256SUMS").read_text()
        self.assertCountEqual(
            [line.split("  ./", 1)[1] for line in manifest.splitlines()],
            [
                "README.md",
                "SQL-RATIONALE.md",
                "bootstrap-args.txt",
                "candidate-access-counts.sql",
                "runner.py",
                "test-output.txt",
                "tests/pass_check.py",
                "tests/run_tests.sh",
                "tests/synthetic_pg.sh",
                "tests/test_runner.py",
            ],
        )

    def test_pinned_payload(self):
        scripts, migrations = contract.candidate_bundle(MASTER)
        self.assertEqual(len(scripts), 2)
        self.assertEqual(len(migrations), 97)
        one = contract.encoded_inputs(MASTER)
        two = contract.encoded_inputs(MASTER)
        self.assertEqual(len(one["CORE_MIGRATIONS_Z"]), 92412)
        self.assertNotEqual(one["CORE_BUNDLE_Z"], two["CORE_BUNDLE_Z"])


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

    def test_non_pinned_admission_without_receipt_uses_existing_gates(self):
        candidate = "a" * 40
        env = self.env | dict.fromkeys(
            ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "ADMITTED_SHA"), candidate
        )
        self.master["object"]["sha"] = candidate
        output = Path(self.enterContext(tempfile.TemporaryDirectory())) / "output"
        for flags in ([], ["--receipt"]):
            with (
                patch.dict(
                    os.environ, env | {"GITHUB_OUTPUT": str(output)}, clear=True
                ),
                patch.object(admission, "api", return_value=self.master) as lookup,
                patch.object(
                    sys,
                    "argv",
                    ["admission", self.workflow, "--admitted", "--output", *flags],
                ),
            ):
                self.assertEqual(admission.main(), 0)
                lookup.assert_called_once_with("git/ref/heads/master")
        self.assertEqual(output.read_text(), f"sha={candidate}\n" * 2)

    def test_pinned_candidate_without_receipt_is_refused(self):
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch.object(
                admission, "api", side_effect=[self.master, {"workflow_runs": []}]
            ) as lookup,
            patch.object(sys, "argv", ["admission", self.workflow, "--receipt"]),
            patch("sys.stdout", io.StringIO()) as output,
        ):
            self.assertEqual(admission.main(), 1)
            self.assertIn("receipt_missing", output.getvalue())
            self.assertIn(f"head_sha={MASTER}", lookup.call_args_list[1].args[0])

    def test_prior_incident_requires_clearance_from_current_master(self):
        workflow = contract.CHECKS_WORKFLOW
        env = self.env | {
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_REF": f"{admission.REPOSITORY}/{admission.WORKFLOW_DIR}"
            f"{workflow}@{admission.REF}",
        }
        history = [
            {"id": 44, "run_attempt": 1, "status": "in_progress", "conclusion": None},
            {
                "id": 43,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 42,
                "run_attempt": 2,
                "status": "completed",
                "conclusion": "success",
                "head_sha": "a" * 40,
            },
        ]
        steps = [
            {"name": admission.CARRIER_STEP, "conclusion": "failure"},
            {"name": admission.RECONCILE_STEP, "conclusion": "failure"},
        ]

        def lookup(path):
            if path == "git/ref/heads/master":
                return self.master
            if path.startswith(f"actions/workflows/{workflow}/runs?"):
                self.assertNotIn("head_sha=", path)
                return {"workflow_runs": history, "total_count": len(history)}
            self.assertEqual(path, "actions/runs/42/attempts/1/jobs?per_page=10")
            return {
                "total_count": 1,
                "jobs": [
                    {
                        "name": admission.EXECUTE_JOB,
                        "conclusion": "failure",
                        "steps": steps,
                    }
                ],
            }

        with patch.object(admission, "git", return_value=b""):
            with self.assertRaisesRegex(
                contract.Stop, "production_checks_incident_requires_clearance"
            ):
                admission.check_context(workflow, env, lookup)
        with patch.object(
            admission,
            "git",
            side_effect=[b"tracked", b'{"42":"Confirmed terminal and reconciled."}'],
        ) as git:
            self.assertEqual(admission.check_context(workflow, env, lookup), MASTER)
            git.assert_called_with("show", f"{MASTER}:{admission.CLEARANCE_FILE}")
        with patch.object(
            admission, "git", side_effect=[b"tracked", b'{"43":"Unrelated run."}']
        ):
            with self.assertRaisesRegex(
                contract.Stop, "production_checks_incident_requires_clearance"
            ):
                admission.check_context(workflow, env, lookup)
        steps[1]["conclusion"] = "success"
        with patch.object(admission, "git", return_value=b""):
            self.assertEqual(admission.check_context(workflow, env, lookup), MASTER)

    def test_receipt_provenance_and_digest(self):
        receipt = contract.receipt_header(MASTER, 42, EXECUTION, "database-url")
        receipt.update(
            template_unchanged=True,
            template_sha256="2" * 64,
            retry_limit=0,
            replica_timeout_seconds=450,
            execution_count=1,
            g2_shape="match",
            access_counts=dict.fromkeys(contract.runner.ACCESS_LABELS, 0),
            preflight={
                "list_executions": True,
                "show_execution": False,
                "no_prior_execution": True,
                "query_workspace": True,
            },
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
        with patch.dict(receipt, preflight={"query_workspace": True}):
            with self.assertRaisesRegex(contract.Stop, "receipt_preflight"):
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


class WorkflowTests(unittest.TestCase):
    def test_only_production_workflows_require_production_checks_receipts(self):
        workflows = contract.REPO_ROOT / admission.WORKFLOW_DIR
        expected = {
            "infra_production_plan_local.yml": {"authorize-production": 1, "plan": 1},
            "infra_apply_local.yml": {"authorize-production": 1, "apply": 2},
            "release_verified_candidate.yml": {"gate": 1, "publish-and-deploy": 2},
            "build_and_publish_local.yml": {
                "release-gates": 1,
                "production-candidate": 1,
                "deploy": 1,
            },
        }
        found = {}
        for workflow in workflows.iterdir():
            if workflow.suffix not in {".yml", ".yaml"}:
                continue
            with self.subTest(workflow=workflow.name):
                _, *jobs = re.split(r"(?m)^  ([\w-]+):\n", workflow.read_text())
                for job, body in zip(jobs[::2], jobs[1::2], strict=True):
                    count = len(re.findall(r"(?m)^.*admission\.py .*--receipt\b", body))
                    if count:
                        self.assertIn(f"admission.py {workflow.name} ", body)
                        self.assertRegex(body, r"(?m)^      actions: read$")
                        found.setdefault(workflow.name, {})[job] = count
        self.assertEqual(found, expected)


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
