"""Offline tests for admission, the pinned runner package, and plan rendering."""

import io
import os
import sys
import unittest
from unittest.mock import patch

import admission
import contract
import render_plan

MASTER = contract.REVIEWED_SOURCE


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


class WorkflowTests(unittest.TestCase):
    def test_workflows_do_not_depend_on_production_checks_receipts(self):
        workflows = contract.REPO_ROOT / admission.WORKFLOW_DIR
        self.assertTrue((workflows / "infra_production_plan_local.yml").is_file())
        self.assertFalse((workflows / "run_production_checks.yml").exists())
        for workflow in workflows.iterdir():
            if workflow.suffix not in {".yml", ".yaml"}:
                continue
            with self.subTest(workflow=workflow.name):
                source = workflow.read_text()
                self.assertNotIn("run_production_checks.yml", source)
                if workflow.name == "staging_restore_rehearsal.yml":
                    # The existing staging cleanup gate takes its own receipt.
                    source = source.replace(
                        "--receipt evidence/cleanup-envelope.json", "", 2
                    )
                self.assertNotIn("--receipt", source)


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
